# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import JsonValue

from relay_teams.automation.automation_service import AutomationService
from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.env.public_webhook_url import (
    build_public_base_url_path,
    is_public_http_url,
)
from relay_teams.env.github_secret_store import get_github_secret_store
from relay_teams.gateway.session_ingress_service import (
    GatewaySessionIngressBusyPolicy,
    GatewaySessionIngressRequest,
    GatewaySessionIngressService,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.monitors import MonitorEventEnvelope, MonitorService, MonitorSourceKind
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.terminal_payload import (
    extract_terminal_error,
    extract_terminal_output,
    parse_terminal_payload_json,
)
from relay_teams.sessions.session_service import SessionService
from relay_teams.triggers.github_client import GitHubApiClient, GitHubApiError
from relay_teams.triggers.models import (
    GitHubActionSpec,
    GitHubActionType,
    GitHubAvailableRepositoryRecord,
    GitHubRepoSubscriptionCreateInput,
    GitHubRepoSubscriptionRecord,
    GitHubRepoSubscriptionUpdateInput,
    GitHubRepoWebhookRegistrationInput,
    GitHubTriggerAccountCreateInput,
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
    GitHubTriggerAccountUpdateInput,
    GitHubWebhookStatus,
    TriggerActionAttemptRecord,
    TriggerActionPhase,
    TriggerActionStatus,
    TriggerDeliveryIngestStatus,
    TriggerDeliveryRecord,
    TriggerDeliverySignatureStatus,
    TriggerDispatchRecord,
    TriggerDispatchStatus,
    TriggerEvaluationRecord,
    TriggerProvider,
    TriggerRuleCreateInput,
    TriggerRuleMatchConfig,
    TriggerRuleRecord,
    TriggerRuleUpdateInput,
    TriggerTargetType,
)
from relay_teams.triggers.repository import (
    GitHubRepoSubscriptionConflictError,
    GitHubTriggerAccountNameConflictError,
    TriggerDeliveryConflictError,
    TriggerRepository,
    TriggerRuleNameConflictError,
)
from relay_teams.triggers.secret_store import GitHubTriggerSecretStore

LOGGER = get_logger(__name__)
_GITHUB_DELIVERY_HEADER = "x-github-delivery"
_GITHUB_EVENT_HEADER = "x-github-event"
_GITHUB_SIGNATURE_HEADER = "x-hub-signature-256"


class EventLogLike(Protocol):
    def list_by_trace_with_ids(
        self, trace_id: str
    ) -> tuple[dict[str, JsonValue], ...]: ...


class GitHubTriggerService:
    def __init__(
        self,
        *,
        config_dir: Path,
        repository: TriggerRepository,
        secret_store: GitHubTriggerSecretStore,
        github_client: GitHubApiClient,
        automation_service: AutomationService,
        session_service: SessionService,
        run_service: SessionRunService,
        run_runtime_repo: RunRuntimeRepository,
        event_log: EventLogLike,
        monitor_service: MonitorService | None = None,
        session_ingress_service: GatewaySessionIngressService | None = None,
        get_github_config: Callable[[], GitHubConfig] | None = None,
    ) -> None:
        self._config_dir = config_dir
        self._repository = repository
        self._secret_store = secret_store
        self._github_client = github_client
        self._automation_service = automation_service
        self._session_service = session_service
        self._run_service = run_service
        self._run_runtime_repo = run_runtime_repo
        self._event_log = event_log
        self._monitor_service = monitor_service
        self._session_ingress_service = session_ingress_service
        self._system_github_secret_store = get_github_secret_store()
        self._get_github_config = (
            (lambda: GitHubConfig()) if get_github_config is None else get_github_config
        )

    def list_accounts(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        return tuple(
            self._resolve_account_record(record)
            for record in self._repository.list_accounts()
        )

    def get_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        return self._resolve_account_record(self._repository.get_account(account_id))

    def create_account(
        self,
        payload: GitHubTriggerAccountCreateInput,
    ) -> GitHubTriggerAccountRecord:
        token = _normalize_optional_text(payload.token)
        if token is None and self._get_system_github_token() is None:
            raise ValueError(
                "token is required unless a system GitHub token is already configured"
            )
        now = _utc_now()
        account_id = f"ghta_{uuid.uuid4().hex[:12]}"
        webhook_secret = _normalize_optional_text(
            payload.webhook_secret
        ) or secrets.token_urlsafe(32)
        record = GitHubTriggerAccountRecord(
            account_id=account_id,
            name=_normalize_required_text(payload.name, field_name="name"),
            display_name=(
                _normalize_optional_text(payload.display_name)
                or _normalize_required_text(payload.name, field_name="name")
            ),
            status=(
                GitHubTriggerAccountStatus.ENABLED
                if payload.enabled
                else GitHubTriggerAccountStatus.DISABLED
            ),
            token_configured=token is not None,
            webhook_secret_configured=True,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_account(record)
        if token is not None:
            self._secret_store.set_token(
                self._config_dir, account_id=account_id, token=token
            )
        self._secret_store.set_webhook_secret(
            self._config_dir,
            account_id=account_id,
            webhook_secret=webhook_secret,
        )
        return self._resolve_account_record(created)

    def update_account(
        self,
        account_id: str,
        payload: GitHubTriggerAccountUpdateInput,
    ) -> GitHubTriggerAccountRecord:
        existing = self._repository.get_account(account_id)
        existing_token = self._secret_store.get_token(
            self._config_dir, account_id=account_id
        )
        existing_webhook_secret = self._secret_store.get_webhook_secret(
            self._config_dir, account_id=account_id
        )
        token = existing_token
        webhook_secret = existing_webhook_secret
        token_updated = False
        webhook_secret_updated = False
        if payload.clear_token:
            token = None
            token_updated = True
        elif "token" in payload.model_fields_set:
            provided_token = _normalize_optional_text(payload.token)
            if provided_token is not None:
                token = provided_token
                token_updated = True
        if payload.clear_webhook_secret:
            webhook_secret = None
            webhook_secret_updated = True
        elif "webhook_secret" in payload.model_fields_set:
            provided_webhook_secret = _normalize_optional_text(payload.webhook_secret)
            if provided_webhook_secret is not None:
                webhook_secret = provided_webhook_secret
                webhook_secret_updated = True
        updated = existing.model_copy(
            update={
                "name": (
                    _normalize_required_text(payload.name, field_name="name")
                    if payload.name is not None
                    else existing.name
                ),
                "display_name": (
                    _normalize_optional_text(payload.display_name)
                    or existing.display_name
                ),
                "status": (
                    GitHubTriggerAccountStatus.ENABLED
                    if payload.enabled is True
                    else (
                        GitHubTriggerAccountStatus.DISABLED
                        if payload.enabled is False
                        else existing.status
                    )
                ),
                "token_configured": (
                    token is not None if token_updated else existing.token_configured
                ),
                "webhook_secret_configured": (
                    webhook_secret is not None
                    if webhook_secret_updated
                    else existing.webhook_secret_configured
                ),
                "updated_at": _utc_now(),
            }
        )
        _ = self._repository.update_account(updated)
        if token_updated:
            self._secret_store.set_token(
                self._config_dir, account_id=account_id, token=token
            )
        if webhook_secret_updated:
            self._secret_store.set_webhook_secret(
                self._config_dir,
                account_id=account_id,
                webhook_secret=webhook_secret,
            )
        if token_updated or webhook_secret_updated or updated.status != existing.status:
            self._reconcile_account_repos(account_id)
        return self._resolve_account_record(self._repository.get_account(account_id))

    def enable_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        return self.update_account(
            account_id,
            GitHubTriggerAccountUpdateInput(enabled=True),
        )

    def disable_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        return self.update_account(
            account_id,
            GitHubTriggerAccountUpdateInput(enabled=False),
        )

    def delete_account(self, account_id: str) -> None:
        _ = self._repository.get_account(account_id)
        for repo_subscription in self._repository.list_repo_subscriptions_by_account(
            account_id
        ):
            self._best_effort_unregister_repo_webhook(repo_subscription)
        self._repository.delete_account(account_id)
        self._secret_store.delete_token(self._config_dir, account_id=account_id)
        self._secret_store.delete_webhook_secret(
            self._config_dir, account_id=account_id
        )

    def list_repo_subscriptions(self) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        return self._repository.list_repo_subscriptions()

    def get_repo_subscription(
        self,
        repo_subscription_id: str,
    ) -> GitHubRepoSubscriptionRecord:
        return self._repository.get_repo_subscription(repo_subscription_id)

    def list_available_repositories(
        self,
        account_id: str,
        *,
        query: str | None = None,
    ) -> tuple[GitHubAvailableRepositoryRecord, ...]:
        _ = self._repository.get_account(account_id)
        token = self._require_account_token(account_id)
        payloads = self._github_client.list_repositories(
            token=token,
            query=_normalize_optional_text(query),
        )
        repositories: list[GitHubAvailableRepositoryRecord] = []
        for payload in payloads:
            owner = _resolve_repository_owner(payload)
            repo_name = _json_text(payload.get("name"))
            if owner is None or repo_name is None:
                continue
            _, _, full_name = _resolve_repository_identity(
                payload,
                owner=owner,
                repo_name=repo_name,
            )
            repositories.append(
                GitHubAvailableRepositoryRecord(
                    owner=owner,
                    repo_name=repo_name,
                    full_name=full_name,
                    default_branch=_json_text(payload.get("default_branch")),
                    private=_json_bool(payload.get("private")) is True,
                )
            )
        return tuple(repositories)

    def create_repo_subscription(
        self,
        payload: GitHubRepoSubscriptionCreateInput,
    ) -> GitHubRepoSubscriptionRecord:
        account = self._repository.get_account(payload.account_id)
        token = self._require_account_token(account.account_id)
        callback_url = _normalize_callback_url(payload.callback_url)
        if callback_url is None:
            callback_url = _build_system_callback_url(
                self._get_github_config().webhook_base_url
            )
        owner = _normalize_required_text(payload.owner, field_name="owner")
        repo_name = _normalize_required_text(payload.repo_name, field_name="repo_name")
        repo_payload = self._github_client.get_repository(
            token=token,
            owner=owner,
            repo=repo_name,
        )
        resolved_owner, resolved_repo_name, resolved_full_name = (
            _resolve_repository_identity(
                repo_payload,
                owner=owner,
                repo_name=repo_name,
            )
        )
        now = _utc_now()
        record = GitHubRepoSubscriptionRecord(
            repo_subscription_id=f"ghrs_{uuid.uuid4().hex[:12]}",
            account_id=account.account_id,
            owner=resolved_owner,
            repo_name=resolved_repo_name,
            full_name=resolved_full_name,
            external_repo_id=_json_identifier(repo_payload.get("id")),
            default_branch=_json_text(repo_payload.get("default_branch")),
            callback_url=callback_url,
            subscribed_events=(),
            webhook_status=GitHubWebhookStatus.UNREGISTERED,
            enabled=payload.enabled,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_repo_subscription(record)
        return self._reconcile_repo_webhook(created.repo_subscription_id)

    def update_repo_subscription(
        self,
        repo_subscription_id: str,
        payload: GitHubRepoSubscriptionUpdateInput,
    ) -> GitHubRepoSubscriptionRecord:
        existing = self._repository.get_repo_subscription(repo_subscription_id)
        owner = (
            _normalize_required_text(payload.owner, field_name="owner")
            if payload.owner is not None
            else existing.owner
        )
        repo_name = (
            _normalize_required_text(payload.repo_name, field_name="repo_name")
            if payload.repo_name is not None
            else existing.repo_name
        )
        callback_url = existing.callback_url
        if "callback_url" in payload.model_fields_set:
            normalized_callback_url = _normalize_callback_url(payload.callback_url)
            if normalized_callback_url is not None:
                callback_url = normalized_callback_url
        identity_changed = owner != existing.owner or repo_name != existing.repo_name
        external_repo_id = existing.external_repo_id
        default_branch = existing.default_branch
        full_name = existing.full_name
        provider_webhook_id = existing.provider_webhook_id
        webhook_status = existing.webhook_status
        last_error = existing.last_error
        if identity_changed:
            token = self._require_account_token(existing.account_id)
            repo_payload = self._github_client.get_repository(
                token=token,
                owner=owner,
                repo=repo_name,
            )
            owner, repo_name, full_name = _resolve_repository_identity(
                repo_payload,
                owner=owner,
                repo_name=repo_name,
            )
            external_repo_id = _json_identifier(repo_payload.get("id"))
            default_branch = _json_text(repo_payload.get("default_branch"))
            if _normalize_optional_text(existing.provider_webhook_id) is not None:
                self._best_effort_unregister_repo_webhook(existing)
            provider_webhook_id = None
            webhook_status = GitHubWebhookStatus.UNREGISTERED
            last_error = None
        updated = existing.model_copy(
            update={
                "owner": owner,
                "repo_name": repo_name,
                "full_name": full_name,
                "external_repo_id": external_repo_id,
                "default_branch": default_branch,
                "callback_url": callback_url,
                "provider_webhook_id": provider_webhook_id,
                "subscribed_events": existing.subscribed_events,
                "webhook_status": webhook_status,
                "enabled": existing.enabled
                if payload.enabled is None
                else payload.enabled,
                "last_error": last_error,
                "updated_at": _utc_now(),
            }
        )
        persisted = self._repository.update_repo_subscription(updated)
        return self._reconcile_repo_webhook(persisted.repo_subscription_id)

    def enable_repo_subscription(
        self,
        repo_subscription_id: str,
    ) -> GitHubRepoSubscriptionRecord:
        return self.update_repo_subscription(
            repo_subscription_id,
            GitHubRepoSubscriptionUpdateInput(enabled=True),
        )

    def disable_repo_subscription(
        self,
        repo_subscription_id: str,
    ) -> GitHubRepoSubscriptionRecord:
        return self.update_repo_subscription(
            repo_subscription_id,
            GitHubRepoSubscriptionUpdateInput(enabled=False),
        )

    def register_repo_webhook(
        self,
        repo_subscription_id: str,
        payload: GitHubRepoWebhookRegistrationInput,
    ) -> GitHubRepoSubscriptionRecord:
        existing = self._repository.get_repo_subscription(repo_subscription_id)
        updated = self._repository.update_repo_subscription(
            existing.model_copy(
                update={
                    "callback_url": _normalize_required_text(
                        payload.callback_url,
                        field_name="callback_url",
                    ),
                    "updated_at": _utc_now(),
                }
            )
        )
        return self._reconcile_repo_webhook(updated.repo_subscription_id)

    def unregister_repo_webhook(
        self, repo_subscription_id: str
    ) -> GitHubRepoSubscriptionRecord:
        existing = self._repository.get_repo_subscription(repo_subscription_id)
        if _normalize_optional_text(existing.provider_webhook_id) is not None:
            token = self._require_account_token(existing.account_id)
            self._github_client.delete_repository_webhook(
                token=token,
                owner=existing.owner,
                repo=existing.repo_name,
                webhook_id=_normalize_required_text(
                    existing.provider_webhook_id,
                    field_name="provider_webhook_id",
                ),
            )
        updated = existing.model_copy(
            update={
                "provider_webhook_id": None,
                "webhook_status": GitHubWebhookStatus.UNREGISTERED,
                "last_webhook_sync_at": _utc_now(),
                "last_error": None,
                "updated_at": _utc_now(),
            }
        )
        return self._repository.update_repo_subscription(updated)

    def delete_repo_subscription(self, repo_subscription_id: str) -> None:
        existing = self._repository.get_repo_subscription(repo_subscription_id)
        self._best_effort_unregister_repo_webhook(existing)
        self._repository.delete_repo_subscription(repo_subscription_id)

    def refresh_repo_callback_urls_from_system_config(
        self,
        *,
        previous_webhook_base_url: str | None = None,
    ) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        current_webhook_base_url = self._get_github_config().webhook_base_url
        previous_callback_url = _build_system_callback_url(previous_webhook_base_url)
        current_callback_url = _build_system_callback_url(current_webhook_base_url)
        updated_records: list[GitHubRepoSubscriptionRecord] = []
        for repo in self._repository.list_repo_subscriptions():
            normalized_callback_url = _normalize_callback_url(repo.callback_url)
            should_replace = (
                normalized_callback_url is None
                or not is_public_http_url(normalized_callback_url)
                or (
                    previous_callback_url is not None
                    and normalized_callback_url == previous_callback_url
                )
            )
            if not should_replace or normalized_callback_url == current_callback_url:
                continue
            updated = self._repository.update_repo_subscription(
                repo.model_copy(
                    update={
                        "callback_url": current_callback_url,
                        "updated_at": _utc_now(),
                    }
                )
            )
            updated_records.append(
                self._reconcile_repo_webhook(updated.repo_subscription_id)
            )
        return tuple(updated_records)

    def list_rules(self) -> tuple[TriggerRuleRecord, ...]:
        return self._repository.list_rules()

    def get_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        return self._repository.get_rule(trigger_rule_id)

    def create_rule(self, payload: TriggerRuleCreateInput) -> TriggerRuleRecord:
        account = self._repository.get_account(payload.account_id)
        repo = self._repository.get_repo_subscription(payload.repo_subscription_id)
        if repo.account_id != account.account_id:
            raise ValueError("repo_subscription_id does not belong to account_id")
        now = _utc_now()
        record = TriggerRuleRecord(
            trigger_rule_id=f"trg_{uuid.uuid4().hex[:12]}",
            provider=payload.provider,
            account_id=account.account_id,
            repo_subscription_id=repo.repo_subscription_id,
            name=_normalize_required_text(payload.name, field_name="name"),
            enabled=payload.enabled,
            match_config=payload.match_config,
            dispatch_config=payload.dispatch_config,
            created_at=now,
            updated_at=now,
        )
        created = self._repository.create_rule(record)
        self._reconcile_repo_webhook(repo.repo_subscription_id)
        return created

    def update_rule(
        self,
        trigger_rule_id: str,
        payload: TriggerRuleUpdateInput,
    ) -> TriggerRuleRecord:
        existing = self._repository.get_rule(trigger_rule_id)
        updated = existing.model_copy(
            update={
                "name": (
                    _normalize_required_text(payload.name, field_name="name")
                    if payload.name is not None
                    else existing.name
                ),
                "enabled": existing.enabled
                if payload.enabled is None
                else payload.enabled,
                "match_config": (
                    existing.match_config
                    if payload.match_config is None
                    else payload.match_config
                ),
                "dispatch_config": (
                    existing.dispatch_config
                    if payload.dispatch_config is None
                    else payload.dispatch_config
                ),
                "version": existing.version + 1,
                "updated_at": _utc_now(),
            }
        )
        persisted = self._repository.update_rule(updated)
        self._reconcile_repo_webhook(existing.repo_subscription_id)
        return persisted

    def enable_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        return self.update_rule(
            trigger_rule_id,
            TriggerRuleUpdateInput(enabled=True),
        )

    def disable_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        return self.update_rule(
            trigger_rule_id,
            TriggerRuleUpdateInput(enabled=False),
        )

    def delete_rule(self, trigger_rule_id: str) -> None:
        existing = self._repository.get_rule(trigger_rule_id)
        self._repository.delete_rule(trigger_rule_id)
        self._reconcile_repo_webhook(existing.repo_subscription_id)

    def list_deliveries(self) -> tuple[TriggerDeliveryRecord, ...]:
        return self._repository.list_deliveries()

    def list_dispatches(self) -> tuple[TriggerDispatchRecord, ...]:
        return self._repository.list_dispatches()

    def list_actions(self) -> tuple[TriggerActionAttemptRecord, ...]:
        return self._repository.list_action_attempts()

    def list_delivery_evaluations(
        self,
        trigger_delivery_id: str,
    ) -> tuple[TriggerEvaluationRecord, ...]:
        return self._repository.list_evaluations_by_delivery(trigger_delivery_id)

    def list_delivery_dispatches(
        self,
        trigger_delivery_id: str,
    ) -> tuple[TriggerDispatchRecord, ...]:
        return self._repository.list_dispatches_by_delivery(trigger_delivery_id)

    def replay_delivery(self, trigger_delivery_id: str) -> dict[str, JsonValue]:
        delivery = self._repository.get_delivery(trigger_delivery_id)
        repo_subscription_id = _normalize_optional_text(delivery.repo_subscription_id)
        if repo_subscription_id is None:
            raise ValueError("delivery does not reference a repository subscription")
        repo = self._repository.get_repo_subscription(repo_subscription_id)
        dispatches = self._evaluate_delivery(
            delivery=delivery,
            repo=repo,
            update_delivery=False,
        )
        return {
            "trigger_delivery_id": delivery.trigger_delivery_id,
            "dispatch_count": len(dispatches),
        }

    def handle_inbound_github_delivery(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, JsonValue]:
        normalized_headers = {
            str(key).lower(): str(value) for key, value in headers.items()
        }
        provider_delivery_id = _normalize_optional_text(
            normalized_headers.get(_GITHUB_DELIVERY_HEADER)
        )
        event_name = _normalize_optional_text(
            normalized_headers.get(_GITHUB_EVENT_HEADER)
        )
        signature = _normalize_optional_text(
            normalized_headers.get(_GITHUB_SIGNATURE_HEADER)
        )
        if provider_delivery_id is not None:
            existing = self._repository.get_delivery_by_provider_id(
                provider=TriggerProvider.GITHUB.value,
                provider_delivery_id=provider_delivery_id,
            )
            if existing is not None:
                return {
                    "trigger_delivery_id": existing.trigger_delivery_id,
                    "ingest_status": TriggerDeliveryIngestStatus.DUPLICATE.value,
                    "dispatch_count": len(
                        self._repository.list_dispatches_by_delivery(
                            existing.trigger_delivery_id
                        )
                    ),
                }
        payload_value, payload_error = _decode_json_payload(body)
        event_value = event_name or "unknown"
        if payload_error is not None:
            delivery = self._create_delivery_or_get_duplicate(
                TriggerDeliveryRecord(
                    trigger_delivery_id=f"tdel_{uuid.uuid4().hex[:12]}",
                    provider=TriggerProvider.GITHUB,
                    provider_delivery_id=provider_delivery_id,
                    event_name=event_value,
                    signature_status=TriggerDeliverySignatureStatus.MISSING
                    if signature is None
                    else TriggerDeliverySignatureStatus.INVALID,
                    ingest_status=TriggerDeliveryIngestStatus.INVALID_HEADERS,
                    headers=normalized_headers,
                    payload={"raw_body": body.decode("utf-8", errors="replace")},
                    normalized_payload={},
                    processed_at=_utc_now(),
                    last_error=payload_error,
                )
            )
            if isinstance(delivery, dict):
                return delivery
            return {
                "trigger_delivery_id": delivery.trigger_delivery_id,
                "ingest_status": delivery.ingest_status.value,
                "dispatch_count": 0,
            }
        repository_full_name = _resolve_repository_full_name(payload_value)
        if repository_full_name is None:
            delivery = self._create_delivery_or_get_duplicate(
                TriggerDeliveryRecord(
                    trigger_delivery_id=f"tdel_{uuid.uuid4().hex[:12]}",
                    provider=TriggerProvider.GITHUB,
                    provider_delivery_id=provider_delivery_id,
                    event_name=event_value,
                    event_action=_resolve_event_action(payload_value),
                    signature_status=TriggerDeliverySignatureStatus.MISSING
                    if signature is None
                    else TriggerDeliverySignatureStatus.INVALID,
                    ingest_status=TriggerDeliveryIngestStatus.INVALID_HEADERS,
                    headers=normalized_headers,
                    payload=payload_value,
                    normalized_payload={},
                    processed_at=_utc_now(),
                    last_error="Missing repository.full_name in GitHub payload",
                )
            )
            if isinstance(delivery, dict):
                return delivery
            return {
                "trigger_delivery_id": delivery.trigger_delivery_id,
                "ingest_status": delivery.ingest_status.value,
                "dispatch_count": 0,
            }
        subscriptions = self._repository.list_repo_subscriptions_by_full_name(
            repository_full_name,
            enabled_only=True,
        )
        subscriptions = self._filter_enabled_account_subscriptions(subscriptions)
        matched_subscription = self._select_subscription_for_signature(
            subscriptions=subscriptions,
            signature=signature,
            body=body,
        )
        signature_status = self._resolve_signature_status(
            matched_subscription=matched_subscription,
            signature=signature,
        )
        normalized_payload = _normalize_github_payload(
            payload_value, event_name=event_value
        )
        delivery = self._create_delivery_or_get_duplicate(
            TriggerDeliveryRecord(
                trigger_delivery_id=f"tdel_{uuid.uuid4().hex[:12]}",
                provider=TriggerProvider.GITHUB,
                provider_delivery_id=provider_delivery_id,
                account_id=(
                    matched_subscription.account_id
                    if matched_subscription is not None
                    else None
                ),
                repo_subscription_id=(
                    matched_subscription.repo_subscription_id
                    if matched_subscription is not None
                    else None
                ),
                event_name=event_value,
                event_action=_resolve_event_action(payload_value),
                signature_status=signature_status,
                ingest_status=TriggerDeliveryIngestStatus.RECEIVED,
                headers=normalized_headers,
                payload=payload_value,
                normalized_payload=normalized_payload,
            )
        )
        if isinstance(delivery, dict):
            return delivery
        if matched_subscription is None:
            updated = self._repository.update_delivery(
                delivery.model_copy(
                    update={
                        "ingest_status": (
                            TriggerDeliveryIngestStatus.UNMATCHED
                            if subscriptions
                            else TriggerDeliveryIngestStatus.INVALID_HEADERS
                        ),
                        "processed_at": _utc_now(),
                        "last_error": (
                            "GitHub webhook signature did not match any configured account"
                            if subscriptions
                            else "No repository subscription matched repository.full_name"
                        ),
                    }
                )
            )
            return {
                "trigger_delivery_id": updated.trigger_delivery_id,
                "ingest_status": updated.ingest_status.value,
                "dispatch_count": 0,
            }
        if signature_status != TriggerDeliverySignatureStatus.VALID:
            updated = self._repository.update_delivery(
                delivery.model_copy(
                    update={
                        "ingest_status": TriggerDeliveryIngestStatus.SIGNATURE_INVALID,
                        "processed_at": _utc_now(),
                        "last_error": "Invalid GitHub webhook signature",
                    }
                )
            )
            return {
                "trigger_delivery_id": updated.trigger_delivery_id,
                "ingest_status": updated.ingest_status.value,
                "dispatch_count": 0,
            }
        self._emit_monitor_event_for_delivery(delivery)
        dispatches = self._evaluate_delivery(
            delivery=delivery,
            repo=matched_subscription,
            update_delivery=True,
        )
        return {
            "trigger_delivery_id": delivery.trigger_delivery_id,
            "ingest_status": (
                TriggerDeliveryIngestStatus.TRIGGERED.value
                if dispatches
                else TriggerDeliveryIngestStatus.UNMATCHED.value
            ),
            "dispatch_count": len(dispatches),
        }

    def process_pending_actions(self) -> bool:
        progress = False
        for attempt in self._repository.list_pending_action_attempts():
            if attempt.status == TriggerActionStatus.WAITING_RUN:
                progress = self._process_waiting_attempt(attempt) or progress
                continue
            progress = self._process_attempt(attempt) or progress
        for dispatch in self._repository.list_open_dispatches():
            progress = self._reconcile_dispatch(dispatch) or progress
        return progress

    def _evaluate_delivery(
        self,
        *,
        delivery: TriggerDeliveryRecord,
        repo: GitHubRepoSubscriptionRecord,
        update_delivery: bool,
    ) -> tuple[TriggerDispatchRecord, ...]:
        rules = self._repository.list_enabled_rules_for_repo(repo.repo_subscription_id)
        dispatches: list[TriggerDispatchRecord] = []
        for rule in rules:
            matched, reason_code, reason_detail = self._match_rule(
                rule.match_config,
                delivery=delivery,
            )
            self._repository.create_evaluation(
                TriggerEvaluationRecord(
                    trigger_evaluation_id=f"teval_{uuid.uuid4().hex[:12]}",
                    trigger_delivery_id=delivery.trigger_delivery_id,
                    trigger_rule_id=rule.trigger_rule_id,
                    matched=matched,
                    reason_code=reason_code,
                    reason_detail=reason_detail,
                )
            )
            if not matched:
                continue
            dispatch = self._create_dispatch(delivery=delivery, rule=rule, repo=repo)
            dispatches.append(dispatch)
            rule_last_error = (
                dispatch.last_error
                if dispatch.status == TriggerDispatchStatus.FAILED
                else None
            )
            self._repository.update_rule(
                rule.model_copy(
                    update={
                        "last_fired_at": _utc_now(),
                        "last_error": rule_last_error,
                        "updated_at": _utc_now(),
                    }
                )
            )
        if update_delivery:
            _ = self._repository.update_delivery(
                delivery.model_copy(
                    update={
                        "ingest_status": (
                            TriggerDeliveryIngestStatus.TRIGGERED
                            if dispatches
                            else TriggerDeliveryIngestStatus.UNMATCHED
                        ),
                        "processed_at": _utc_now(),
                        "last_error": None,
                    }
                )
            )
        return tuple(dispatches)

    def _create_dispatch(
        self,
        *,
        delivery: TriggerDeliveryRecord,
        rule: TriggerRuleRecord,
        repo: GitHubRepoSubscriptionRecord,
    ) -> TriggerDispatchRecord:
        now = _utc_now()
        base_dispatch = TriggerDispatchRecord(
            trigger_dispatch_id=f"tdsp_{uuid.uuid4().hex[:12]}",
            trigger_delivery_id=delivery.trigger_delivery_id,
            trigger_rule_id=rule.trigger_rule_id,
            target_type=rule.dispatch_config.target_type,
            status=TriggerDispatchStatus.PENDING,
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        dispatch = self._repository.create_dispatch(base_dispatch)
        try:
            if rule.dispatch_config.target_type == TriggerTargetType.AUTOMATION_PROJECT:
                automation_project_id = _normalize_required_text(
                    rule.dispatch_config.automation_project_id,
                    field_name="automation_project_id",
                )
                result = self._automation_service.run_now(automation_project_id)
                updated = dispatch.model_copy(
                    update={
                        "automation_project_id": automation_project_id,
                        "session_id": _normalize_optional_text(
                            result.get("session_id")
                        ),
                        "run_id": _normalize_optional_text(result.get("run_id")),
                        "status": (
                            TriggerDispatchStatus.RUNNING
                            if _normalize_optional_text(result.get("run_id"))
                            is not None
                            else TriggerDispatchStatus.COMPLETED
                        ),
                        "updated_at": _utc_now(),
                    }
                )
            else:
                run_template = rule.dispatch_config.run_template
                if run_template is None:
                    raise RuntimeError("missing_run_template")
                session_id, run_id = self._start_run_template(
                    delivery=delivery,
                    repo=repo,
                    rule=rule,
                )
                updated = dispatch.model_copy(
                    update={
                        "session_id": session_id,
                        "run_id": run_id,
                        "status": TriggerDispatchStatus.RUNNING,
                        "updated_at": _utc_now(),
                    }
                )
            dispatch = self._repository.update_dispatch(updated)
            attempts = self._create_action_attempts(dispatch=dispatch, rule=rule)
            if dispatch.run_id is None and not attempts:
                dispatch = self._repository.update_dispatch(
                    dispatch.model_copy(
                        update={
                            "status": TriggerDispatchStatus.COMPLETED,
                            "completed_at": _utc_now(),
                            "updated_at": _utc_now(),
                        }
                    )
                )
            return dispatch
        except Exception as exc:
            failed = self._repository.update_dispatch(
                dispatch.model_copy(
                    update={
                        "status": TriggerDispatchStatus.FAILED,
                        "completed_at": _utc_now(),
                        "last_error": str(exc),
                        "updated_at": _utc_now(),
                    }
                )
            )
            self._repository.update_rule(
                rule.model_copy(
                    update={
                        "last_error": str(exc),
                        "updated_at": _utc_now(),
                    }
                )
            )
            return failed

    def _start_run_template(
        self,
        *,
        delivery: TriggerDeliveryRecord,
        repo: GitHubRepoSubscriptionRecord,
        rule: TriggerRuleRecord,
    ) -> tuple[str, str]:
        run_template = rule.dispatch_config.run_template
        if run_template is None:
            raise RuntimeError("missing_run_template")
        prompt = _render_template(
            run_template.prompt_template,
            self._build_template_context(delivery=delivery, dispatch=None, repo=repo),
        )
        session = self._session_service.create_session(
            workspace_id=run_template.workspace_id,
            metadata={
                "title": _build_dispatch_title(delivery),
                "github_repository": repo.full_name,
                "github_event": delivery.event_name,
                "github_action": str(delivery.event_action or ""),
                "github_delivery_id": delivery.trigger_delivery_id,
            },
            session_mode=run_template.session_mode,
            normal_root_role_id=run_template.normal_root_role_id,
            orchestration_preset_id=run_template.orchestration_preset_id,
        )
        intent = IntentInput(
            session_id=session.session_id,
            input=content_parts_from_text(prompt),
            execution_mode=run_template.execution_mode,
            yolo=run_template.yolo,
            thinking=run_template.thinking,
            session_mode=run_template.session_mode,
        )
        if self._session_ingress_service is not None:
            ingress_result = self._session_ingress_service.require_started(
                GatewaySessionIngressRequest(
                    intent=intent,
                    busy_policy=GatewaySessionIngressBusyPolicy.START_IF_IDLE,
                )
            )
            if ingress_result.run_id is None:
                raise RuntimeError("trigger_run_not_started")
            return session.session_id, ingress_result.run_id
        run_id, _session_id = self._run_service.create_run(intent)
        self._run_service.ensure_run_started(run_id)
        return session.session_id, run_id

    def _create_action_attempts(
        self,
        *,
        dispatch: TriggerDispatchRecord,
        rule: TriggerRuleRecord,
    ) -> tuple[TriggerActionAttemptRecord, ...]:
        created: list[TriggerActionAttemptRecord] = []
        for action_spec in rule.dispatch_config.action_hooks:
            status = TriggerActionStatus.PENDING
            if action_spec.phase != TriggerActionPhase.IMMEDIATE:
                status = (
                    TriggerActionStatus.WAITING_RUN
                    if dispatch.run_id is not None
                    else TriggerActionStatus.SKIPPED
                )
            record = TriggerActionAttemptRecord(
                trigger_action_attempt_id=f"tact_{uuid.uuid4().hex[:12]}",
                trigger_dispatch_id=dispatch.trigger_dispatch_id,
                phase=action_spec.phase,
                action_type=action_spec.action_type,
                status=status,
                action_spec=action_spec,
                created_at=_utc_now(),
                updated_at=_utc_now(),
            )
            created.append(self._repository.create_action_attempt(record))
        return tuple(created)

    def _process_waiting_attempt(self, attempt: TriggerActionAttemptRecord) -> bool:
        dispatch = self._repository.get_dispatch(attempt.trigger_dispatch_id)
        run_id = _normalize_optional_text(dispatch.run_id)
        if run_id is None:
            skipped = self._repository.update_action_attempt(
                attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.SKIPPED,
                        "last_error": "dispatch has no run_id",
                        "updated_at": _utc_now(),
                    }
                )
            )
            _ = skipped
            return True
        runtime = self._run_runtime_repo.get(run_id)
        if runtime is None or runtime.status not in {
            RunRuntimeStatus.COMPLETED,
            RunRuntimeStatus.FAILED,
        }:
            return False
        if (
            runtime.status == RunRuntimeStatus.COMPLETED
            and attempt.phase != TriggerActionPhase.ON_RUN_COMPLETED
        ):
            _ = self._repository.update_action_attempt(
                attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.SKIPPED,
                        "last_error": None,
                        "updated_at": _utc_now(),
                    }
                )
            )
            return True
        if (
            runtime.status == RunRuntimeStatus.FAILED
            and attempt.phase != TriggerActionPhase.ON_RUN_FAILED
        ):
            _ = self._repository.update_action_attempt(
                attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.SKIPPED,
                        "last_error": None,
                        "updated_at": _utc_now(),
                    }
                )
            )
            return True
        return self._process_attempt(
            attempt.model_copy(update={"status": TriggerActionStatus.PENDING})
        )

    def _process_attempt(self, attempt: TriggerActionAttemptRecord) -> bool:
        if attempt.status not in {
            TriggerActionStatus.PENDING,
            TriggerActionStatus.SENDING,
        }:
            return False
        dispatch = self._repository.get_dispatch(attempt.trigger_dispatch_id)
        delivery = self._repository.get_delivery(dispatch.trigger_delivery_id)
        repo_subscription_id = _normalize_optional_text(delivery.repo_subscription_id)
        if repo_subscription_id is None:
            updated = self._repository.update_action_attempt(
                attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.FAILED,
                        "attempt_count": attempt.attempt_count + 1,
                        "last_error": "delivery is missing repo_subscription_id",
                        "updated_at": _utc_now(),
                    }
                )
            )
            _ = updated
            return True
        repo = self._repository.get_repo_subscription(repo_subscription_id)
        context = self._build_template_context(
            delivery=delivery,
            dispatch=dispatch,
            repo=repo,
        )
        next_attempt = attempt.model_copy(
            update={
                "status": TriggerActionStatus.SENDING,
                "attempt_count": attempt.attempt_count + 1,
                "updated_at": _utc_now(),
            }
        )
        self._repository.update_action_attempt(next_attempt)
        try:
            token = self._require_account_token(repo.account_id)
            request_payload, response_payload, resource_id = self._execute_action(
                action_spec=attempt.action_spec,
                token=token,
                repo=repo,
                context=context,
            )
            persisted = self._repository.update_action_attempt(
                next_attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.SUCCEEDED,
                        "request_payload": request_payload,
                        "response_payload": response_payload,
                        "provider_resource_id": resource_id,
                        "last_error": None,
                        "updated_at": _utc_now(),
                    }
                )
            )
            _ = persisted
        except Exception as exc:
            persisted = self._repository.update_action_attempt(
                next_attempt.model_copy(
                    update={
                        "status": TriggerActionStatus.FAILED,
                        "last_error": str(exc),
                        "updated_at": _utc_now(),
                    }
                )
            )
            _ = persisted
        return True

    def _reconcile_dispatch(self, dispatch: TriggerDispatchRecord) -> bool:
        attempts = self._repository.list_action_attempts_by_dispatch(
            dispatch.trigger_dispatch_id
        )
        if any(
            attempt.status
            in {
                TriggerActionStatus.PENDING,
                TriggerActionStatus.WAITING_RUN,
                TriggerActionStatus.SENDING,
            }
            for attempt in attempts
        ):
            return False
        next_status: TriggerDispatchStatus
        run_id = _normalize_optional_text(dispatch.run_id)
        runtime = self._run_runtime_repo.get(run_id) if run_id is not None else None
        if runtime is not None:
            if runtime.status == RunRuntimeStatus.COMPLETED:
                next_status = TriggerDispatchStatus.COMPLETED
            elif runtime.status == RunRuntimeStatus.FAILED:
                next_status = TriggerDispatchStatus.FAILED
            else:
                return False
        elif any(attempt.status == TriggerActionStatus.FAILED for attempt in attempts):
            next_status = TriggerDispatchStatus.FAILED
        else:
            next_status = TriggerDispatchStatus.COMPLETED
        if dispatch.status == next_status and dispatch.completed_at is not None:
            return False
        _ = self._repository.update_dispatch(
            dispatch.model_copy(
                update={
                    "status": next_status,
                    "completed_at": _utc_now(),
                    "last_error": (
                        dispatch.last_error
                        if next_status != TriggerDispatchStatus.FAILED
                        else (
                            dispatch.last_error
                            or _resolve_failed_attempt_error(attempts)
                        )
                    ),
                    "updated_at": _utc_now(),
                }
            )
        )
        return True

    def _execute_action(
        self,
        *,
        action_spec: GitHubActionSpec,
        token: str,
        repo: GitHubRepoSubscriptionRecord,
        context: dict[str, str],
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue], str | None]:
        issue_number = _parse_int(context.get("issue_number"))
        if issue_number is None:
            issue_number = _parse_int(context.get("pull_request_number"))
        if action_spec.action_type == GitHubActionType.COMMENT:
            if issue_number is None:
                raise RuntimeError("issue_or_pull_request_number_missing")
            body = _render_template(
                _normalize_required_text(
                    action_spec.body_template, field_name="body_template"
                ),
                context,
            )
            response = self._github_client.create_issue_comment(
                token=token,
                owner=repo.owner,
                repo=repo.repo_name,
                issue_number=issue_number,
                body=body,
            )
            response_payload = _response_payload_dict(response)
            response_id = _json_identifier(response_payload.get("id"))
            return {"body": body}, response_payload, response_id
        if action_spec.action_type == GitHubActionType.ADD_LABELS:
            if issue_number is None:
                raise RuntimeError("issue_or_pull_request_number_missing")
            response = self._github_client.add_labels(
                token=token,
                owner=repo.owner,
                repo=repo.repo_name,
                issue_number=issue_number,
                labels=action_spec.labels,
            )
            request_payload = {"labels": _json_string_list_value(action_spec.labels)}
            return request_payload, _response_payload_dict(response), None
        if action_spec.action_type == GitHubActionType.REMOVE_LABELS:
            if issue_number is None:
                raise RuntimeError("issue_or_pull_request_number_missing")
            for label in action_spec.labels:
                self._github_client.remove_label(
                    token=token,
                    owner=repo.owner,
                    repo=repo.repo_name,
                    issue_number=issue_number,
                    label=label,
                )
            return {"labels": _json_string_list_value(action_spec.labels)}, {}, None
        if action_spec.action_type == GitHubActionType.ASSIGN_USERS:
            if issue_number is None:
                raise RuntimeError("issue_or_pull_request_number_missing")
            response = self._github_client.add_assignees(
                token=token,
                owner=repo.owner,
                repo=repo.repo_name,
                issue_number=issue_number,
                assignees=action_spec.assignees,
            )
            request_payload = {
                "assignees": _json_string_list_value(action_spec.assignees)
            }
            return request_payload, _response_payload_dict(response), None
        if action_spec.action_type == GitHubActionType.UNASSIGN_USERS:
            if issue_number is None:
                raise RuntimeError("issue_or_pull_request_number_missing")
            response = self._github_client.remove_assignees(
                token=token,
                owner=repo.owner,
                repo=repo.repo_name,
                issue_number=issue_number,
                assignees=action_spec.assignees,
            )
            request_payload = {
                "assignees": _json_string_list_value(action_spec.assignees)
            }
            return request_payload, _response_payload_dict(response), None
        if action_spec.action_type == GitHubActionType.SET_COMMIT_STATUS:
            sha = _normalize_optional_text(context.get("head_sha"))
            if sha is None:
                raise RuntimeError("head_sha_missing")
            description_template = _normalize_optional_text(
                action_spec.commit_status_description_template
            )
            target_url_template = _normalize_optional_text(
                action_spec.commit_status_target_url_template
            )
            description = (
                _render_template(description_template, context)
                if description_template is not None
                else None
            )
            target_url = (
                _render_template(target_url_template, context)
                if target_url_template is not None
                else None
            )
            response = self._github_client.set_commit_status(
                token=token,
                owner=repo.owner,
                repo=repo.repo_name,
                sha=sha,
                state=_normalize_required_text(
                    action_spec.commit_status_state, field_name="commit_status_state"
                ),
                context=_normalize_required_text(
                    action_spec.commit_status_context,
                    field_name="commit_status_context",
                ),
                description=description,
                target_url=target_url,
            )
            response_payload = _response_payload_dict(response)
            request_payload: dict[str, JsonValue] = {
                "sha": sha,
                "state": action_spec.commit_status_state,
                "context": action_spec.commit_status_context,
                "description": description,
                "target_url": target_url,
            }
            response_id = _json_identifier(response_payload.get("id"))
            return request_payload, response_payload, response_id
        raise RuntimeError(f"Unsupported action_type: {action_spec.action_type.value}")

    @staticmethod
    def _match_rule(
        match_config: TriggerRuleMatchConfig,
        *,
        delivery: TriggerDeliveryRecord,
    ) -> tuple[bool, str, str | None]:
        normalized = delivery.normalized_payload
        if delivery.event_name != match_config.event_name:
            return False, "event_mismatch", "event_name did not match"
        event_action = _normalize_optional_text(delivery.event_action)
        if match_config.actions and event_action not in match_config.actions:
            return False, "action_mismatch", "event_action did not match"
        base_branch = _normalize_optional_text(
            _json_text(normalized.get("base_branch"))
        )
        if match_config.base_branches and base_branch not in match_config.base_branches:
            return False, "base_branch_mismatch", "base_branch did not match"
        if match_config.draft_pr is not None:
            draft_pr = _json_bool(normalized.get("draft_pr"))
            if draft_pr is None or draft_pr != match_config.draft_pr:
                return False, "draft_pr_mismatch", "draft_pr did not match"
        conclusion = _normalize_optional_text(
            _json_text(normalized.get("check_conclusion"))
        )
        if (
            match_config.check_conclusions
            and conclusion not in match_config.check_conclusions
        ):
            return False, "check_conclusion_mismatch", "check_conclusion did not match"
        return True, "matched", None

    def _best_effort_unregister_repo_webhook(
        self,
        repo_subscription: GitHubRepoSubscriptionRecord,
    ) -> None:
        webhook_id = _normalize_optional_text(repo_subscription.provider_webhook_id)
        if webhook_id is None:
            return
        try:
            token = self._require_account_token(repo_subscription.account_id)
            self._github_client.delete_repository_webhook(
                token=token,
                owner=repo_subscription.owner,
                repo=repo_subscription.repo_name,
                webhook_id=webhook_id,
            )
        except (GitHubApiError, KeyError, ValueError) as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="github.trigger.webhook_cleanup_failed",
                message="Failed to unregister GitHub webhook during trigger cleanup",
                payload={
                    "repo_subscription_id": repo_subscription.repo_subscription_id,
                    "account_id": repo_subscription.account_id,
                    "owner": repo_subscription.owner,
                    "repo_name": repo_subscription.repo_name,
                    "webhook_id": webhook_id,
                    "error": str(exc),
                },
            )

    def _reconcile_account_repos(self, account_id: str) -> None:
        for repo_subscription in self._repository.list_repo_subscriptions_by_account(
            account_id
        ):
            self._reconcile_repo_webhook(repo_subscription.repo_subscription_id)

    def _reconcile_repo_webhook(
        self,
        repo_subscription_id: str,
    ) -> GitHubRepoSubscriptionRecord:
        existing = self._repository.get_repo_subscription(repo_subscription_id)
        desired_events = self._derive_subscribed_events_for_repo(repo_subscription_id)
        account = self._repository.get_account(existing.account_id)
        if (
            account.status != GitHubTriggerAccountStatus.ENABLED
            or not existing.enabled
            or not desired_events
        ):
            return self._unregister_repo_webhook_for_reconcile(
                existing,
                desired_events=desired_events,
            )

        callback_url = _normalize_callback_url(existing.callback_url)
        if callback_url is None:
            return self._repository.update_repo_subscription(
                existing.model_copy(
                    update={
                        "subscribed_events": desired_events,
                        "provider_webhook_id": None,
                        "webhook_status": GitHubWebhookStatus.ERROR,
                        "last_webhook_sync_at": _utc_now(),
                        "last_error": (
                            "callback_url is required to register a GitHub webhook; "
                            "configure a public GitHub webhook base URL or set a repo callback_url"
                        ),
                        "updated_at": _utc_now(),
                    }
                )
            )
        try:
            token = self._require_account_token(existing.account_id)
            webhook_secret = self._require_account_webhook_secret(existing.account_id)
            webhook_id = _normalize_optional_text(existing.provider_webhook_id)
            if webhook_id is not None:
                self._github_client.delete_repository_webhook(
                    token=token,
                    owner=existing.owner,
                    repo=existing.repo_name,
                    webhook_id=webhook_id,
                )
            response_payload = self._github_client.register_repository_webhook(
                token=token,
                owner=existing.owner,
                repo=existing.repo_name,
                callback_url=callback_url,
                webhook_secret=webhook_secret,
                events=desired_events,
            )
            updated = existing.model_copy(
                update={
                    "callback_url": callback_url,
                    "provider_webhook_id": _json_identifier(response_payload.get("id")),
                    "subscribed_events": desired_events,
                    "webhook_status": GitHubWebhookStatus.REGISTERED,
                    "last_webhook_sync_at": _utc_now(),
                    "last_error": None,
                    "updated_at": _utc_now(),
                }
            )
        except (GitHubApiError, KeyError, ValueError) as exc:
            updated = existing.model_copy(
                update={
                    "callback_url": callback_url,
                    "subscribed_events": desired_events,
                    "provider_webhook_id": None,
                    "webhook_status": GitHubWebhookStatus.ERROR,
                    "last_webhook_sync_at": _utc_now(),
                    "last_error": str(exc),
                    "updated_at": _utc_now(),
                }
            )
        return self._repository.update_repo_subscription(updated)

    def _derive_subscribed_events_for_repo(
        self,
        repo_subscription_id: str,
    ) -> tuple[str, ...]:
        seen: set[str] = set()
        normalized: list[str] = []
        for rule in self._repository.list_enabled_rules_for_repo(repo_subscription_id):
            event_name = _normalize_optional_text(rule.match_config.event_name)
            if event_name is None or event_name in seen:
                continue
            seen.add(event_name)
            normalized.append(event_name)
        return tuple(normalized)

    def _unregister_repo_webhook_for_reconcile(
        self,
        repo_subscription: GitHubRepoSubscriptionRecord,
        *,
        desired_events: tuple[str, ...],
    ) -> GitHubRepoSubscriptionRecord:
        webhook_id = _normalize_optional_text(repo_subscription.provider_webhook_id)
        if webhook_id is None:
            return self._repository.update_repo_subscription(
                repo_subscription.model_copy(
                    update={
                        "subscribed_events": desired_events,
                        "webhook_status": GitHubWebhookStatus.UNREGISTERED,
                        "last_webhook_sync_at": _utc_now(),
                        "last_error": None,
                        "updated_at": _utc_now(),
                    }
                )
            )
        try:
            token = self._require_account_token(repo_subscription.account_id)
            self._github_client.delete_repository_webhook(
                token=token,
                owner=repo_subscription.owner,
                repo=repo_subscription.repo_name,
                webhook_id=webhook_id,
            )
            updated = repo_subscription.model_copy(
                update={
                    "provider_webhook_id": None,
                    "subscribed_events": desired_events,
                    "webhook_status": GitHubWebhookStatus.UNREGISTERED,
                    "last_webhook_sync_at": _utc_now(),
                    "last_error": None,
                    "updated_at": _utc_now(),
                }
            )
        except (GitHubApiError, KeyError, ValueError) as exc:
            updated = repo_subscription.model_copy(
                update={
                    "subscribed_events": desired_events,
                    "webhook_status": GitHubWebhookStatus.ERROR,
                    "last_webhook_sync_at": _utc_now(),
                    "last_error": str(exc),
                    "updated_at": _utc_now(),
                }
            )
        return self._repository.update_repo_subscription(updated)

    def _build_template_context(
        self,
        *,
        delivery: TriggerDeliveryRecord,
        dispatch: TriggerDispatchRecord | None,
        repo: GitHubRepoSubscriptionRecord,
    ) -> dict[str, str]:
        context: dict[str, str] = {
            "event_name": delivery.event_name,
            "event_action": str(delivery.event_action or ""),
            "repository_owner": repo.owner,
            "repository_name": repo.repo_name,
            "repository_full_name": repo.full_name,
            "delivery_id": delivery.trigger_delivery_id,
        }
        for key, value in delivery.normalized_payload.items():
            context[str(key)] = _stringify_json_value(value)
        if dispatch is not None:
            context["dispatch_id"] = dispatch.trigger_dispatch_id
            context["session_id"] = str(dispatch.session_id or "")
            context["run_id"] = str(dispatch.run_id or "")
            context.update(self._build_run_context(dispatch.run_id))
        return context

    def _build_run_context(self, run_id: str | None) -> dict[str, str]:
        normalized_run_id = _normalize_optional_text(run_id)
        if normalized_run_id is None:
            return {}
        runtime = self._run_runtime_repo.get(normalized_run_id)
        context: dict[str, str] = {
            "run_status": runtime.status.value if runtime is not None else "",
            "run_error": runtime.last_error
            if runtime is not None and runtime.last_error is not None
            else "",
            "run_output": "",
        }
        for event in reversed(
            self._event_log.list_by_trace_with_ids(normalized_run_id)
        ):
            event_type_value = event.get("event_type")
            if event_type_value not in {
                RunEventType.RUN_COMPLETED.value,
                RunEventType.RUN_FAILED.value,
            }:
                continue
            payload = parse_terminal_payload_json(event.get("payload_json"))
            context["run_output"] = extract_terminal_output(payload)
            context["run_error"] = (
                extract_terminal_error(payload) or context["run_error"]
            )
            break
        return context

    def _select_subscription_for_signature(
        self,
        *,
        subscriptions: tuple[GitHubRepoSubscriptionRecord, ...],
        signature: str | None,
        body: bytes,
    ) -> GitHubRepoSubscriptionRecord | None:
        if not subscriptions:
            return None
        if signature is None:
            return subscriptions[0]
        for subscription in subscriptions:
            webhook_secret = self._secret_store.get_webhook_secret(
                self._config_dir,
                account_id=subscription.account_id,
            )
            if webhook_secret is None:
                continue
            if _validate_signature(
                body=body, signature=signature, secret=webhook_secret
            ):
                return subscription
        return None

    def _create_delivery_or_get_duplicate(
        self,
        record: TriggerDeliveryRecord,
    ) -> TriggerDeliveryRecord | dict[str, JsonValue]:
        try:
            return self._repository.create_delivery(record)
        except TriggerDeliveryConflictError:
            provider_delivery_id = _normalize_optional_text(record.provider_delivery_id)
            if provider_delivery_id is None:
                raise
            existing = self._repository.get_delivery_by_provider_id(
                provider=record.provider.value,
                provider_delivery_id=provider_delivery_id,
            )
            if existing is None:
                raise
            return {
                "trigger_delivery_id": existing.trigger_delivery_id,
                "ingest_status": TriggerDeliveryIngestStatus.DUPLICATE.value,
                "dispatch_count": len(
                    self._repository.list_dispatches_by_delivery(
                        existing.trigger_delivery_id
                    )
                ),
            }

    def _filter_enabled_account_subscriptions(
        self,
        subscriptions: tuple[GitHubRepoSubscriptionRecord, ...],
    ) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        enabled: list[GitHubRepoSubscriptionRecord] = []
        for subscription in subscriptions:
            try:
                account = self._repository.get_account(subscription.account_id)
            except KeyError as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="trigger.account.missing",
                    message="Skipping GitHub repo subscription with missing account",
                    payload={
                        "account_id": subscription.account_id,
                        "repo_subscription_id": subscription.repo_subscription_id,
                    },
                    exc_info=exc,
                )
                continue
            if account.status != GitHubTriggerAccountStatus.ENABLED:
                continue
            enabled.append(subscription)
        return tuple(enabled)

    @staticmethod
    def _resolve_signature_status(
        *,
        matched_subscription: GitHubRepoSubscriptionRecord | None,
        signature: str | None,
    ) -> TriggerDeliverySignatureStatus:
        if signature is None:
            return TriggerDeliverySignatureStatus.MISSING
        if matched_subscription is None:
            return TriggerDeliverySignatureStatus.INVALID
        return TriggerDeliverySignatureStatus.VALID

    def _require_account_token(self, account_id: str) -> str:
        token = self._secret_store.get_token(self._config_dir, account_id=account_id)
        if token is None:
            token = self._get_system_github_token()
        if token is None:
            raise ValueError(f"GitHub token is not configured for account {account_id}")
        return token

    def _require_account_webhook_secret(self, account_id: str) -> str:
        webhook_secret = self._secret_store.get_webhook_secret(
            self._config_dir,
            account_id=account_id,
        )
        if webhook_secret is None:
            raise ValueError(
                f"GitHub webhook secret is not configured for account {account_id}"
            )
        return webhook_secret

    def _resolve_account_record(
        self,
        record: GitHubTriggerAccountRecord,
    ) -> GitHubTriggerAccountRecord:
        return record.model_copy(
            update={
                "token_configured": (
                    record.token_configured
                    or self._get_system_github_token() is not None
                )
            }
        )

    def _get_system_github_token(self) -> str | None:
        return self._system_github_secret_store.get_token(self._config_dir)

    def _emit_monitor_event_for_delivery(self, delivery: TriggerDeliveryRecord) -> None:
        if self._monitor_service is None:
            return
        repository_full_name = _normalize_optional_text(
            delivery.normalized_payload.get("repository_full_name")
        )
        if repository_full_name is None:
            return
        event_name = _monitor_event_name_for_delivery(delivery)
        if event_name is None:
            return
        attributes = _monitor_attributes_from_normalized_payload(
            delivery.normalized_payload
        )
        attributes["repository_full_name"] = repository_full_name
        if delivery.event_action is not None and delivery.event_action.strip():
            attributes["event_action"] = delivery.event_action
        self._monitor_service.emit(
            MonitorEventEnvelope(
                source_kind=MonitorSourceKind.GITHUB,
                source_key=repository_full_name,
                event_name=event_name,
                body_text=_monitor_body_text_for_delivery(delivery),
                attributes=attributes,
                dedupe_key=delivery.provider_delivery_id
                or delivery.trigger_delivery_id,
                raw_payload_json=json.dumps(
                    delivery.normalized_payload,
                    ensure_ascii=False,
                ),
                occurred_at=delivery.received_at,
            )
        )


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _normalize_required_text(value: object, *, field_name: str) -> str:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_callback_url(value: object) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    parts = urlsplit(normalized)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("callback_url must be an absolute http or https URL")
    return normalized


def _build_system_callback_url(webhook_base_url: str | None) -> str | None:
    normalized_base_url = _normalize_optional_text(webhook_base_url)
    if normalized_base_url is None:
        return None
    return build_public_base_url_path(
        normalized_base_url,
        "/api/triggers/github/deliveries",
    )


def _normalize_events(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = _normalize_optional_text(value)
        if normalized_value is None or normalized_value in seen:
            continue
        seen.add(normalized_value)
        normalized.append(normalized_value)
    return tuple(normalized)


def _decode_json_payload(body: bytes) -> tuple[dict[str, JsonValue], str | None]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, str(exc)
    if not isinstance(payload, dict):
        return {}, "GitHub webhook payload must be a JSON object"
    return _normalize_json_mapping(payload), None


def _normalize_json_mapping(value: dict[str, object]) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            normalized[str(key)] = item
            continue
        if isinstance(item, dict):
            normalized[str(key)] = _normalize_json_mapping(item)
            continue
        if isinstance(item, list):
            normalized[str(key)] = _normalize_json_list(item)
            continue
        normalized[str(key)] = str(item)
    return normalized


def _normalize_json_list(value: list[object]) -> list[JsonValue]:
    normalized: list[JsonValue] = []
    for item in value:
        if isinstance(item, (str, int, float, bool)) or item is None:
            normalized.append(item)
            continue
        if isinstance(item, dict):
            normalized.append(_normalize_json_mapping(item))
            continue
        if isinstance(item, list):
            normalized.append(_normalize_json_list(item))
            continue
        normalized.append(str(item))
    return normalized


def _resolve_repository_full_name(payload: dict[str, JsonValue]) -> str | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return None
    full_name = repository.get("full_name")
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    owner = repository.get("owner")
    owner_login = None
    if isinstance(owner, dict):
        raw_login = owner.get("login")
        if isinstance(raw_login, str) and raw_login.strip():
            owner_login = raw_login.strip()
    repo_name = repository.get("name")
    if owner_login is None or not isinstance(repo_name, str) or not repo_name.strip():
        return None
    return f"{owner_login}/{repo_name.strip()}"


def _resolve_repository_identity(
    payload: dict[str, JsonValue],
    *,
    owner: str,
    repo_name: str,
) -> tuple[str, str, str]:
    resolved_full_name = _normalize_optional_text(_json_text(payload.get("full_name")))
    if resolved_full_name is not None:
        owner_part, _, repo_part = resolved_full_name.partition("/")
        if owner_part and repo_part:
            return owner_part, repo_part, resolved_full_name

    resolved_owner = _normalize_optional_text(_nested_text(payload, "owner", "login"))
    resolved_repo_name = _normalize_optional_text(_json_text(payload.get("name")))
    if resolved_owner is not None and resolved_repo_name is not None:
        resolved_full_name = f"{resolved_owner}/{resolved_repo_name}"
        return resolved_owner, resolved_repo_name, resolved_full_name
    return owner, repo_name, f"{owner}/{repo_name}"


def _resolve_repository_owner(payload: dict[str, JsonValue]) -> str | None:
    return _normalize_optional_text(_nested_text(payload, "owner", "login"))


def _resolve_event_action(payload: dict[str, JsonValue]) -> str | None:
    action = payload.get("action")
    if isinstance(action, str) and action.strip():
        return action.strip()
    return None


def _normalize_github_payload(
    payload: dict[str, JsonValue],
    *,
    event_name: str,
) -> dict[str, JsonValue]:
    repository_full_name = _resolve_repository_full_name(payload) or ""
    sender = payload.get("sender")
    sender_login = ""
    if isinstance(sender, dict):
        raw_login = sender.get("login")
        if isinstance(raw_login, str):
            sender_login = raw_login.strip()
    normalized: dict[str, JsonValue] = {
        "repository_full_name": repository_full_name,
        "sender_login": sender_login,
        "event_action": _resolve_event_action(payload) or "",
    }
    if event_name == "pull_request":
        pull_request = payload.get("pull_request")
        if isinstance(pull_request, dict):
            normalized["pull_request_number"] = payload.get("number")
            normalized["issue_number"] = payload.get("number")
            normalized["title"] = pull_request.get("title")
            normalized["body"] = pull_request.get("body")
            normalized["html_url"] = pull_request.get("html_url")
            normalized["draft_pr"] = pull_request.get("draft")
            normalized["base_branch"] = _nested_text(pull_request, "base", "ref")
            normalized["head_branch"] = _nested_text(pull_request, "head", "ref")
            normalized["head_sha"] = _nested_text(pull_request, "head", "sha")
            normalized["label_names"] = _json_string_list_value(
                _extract_label_names(pull_request.get("labels"))
            )
    elif event_name == "issues":
        issue = payload.get("issue")
        if isinstance(issue, dict):
            normalized["issue_number"] = issue.get("number")
            normalized["title"] = issue.get("title")
            normalized["body"] = issue.get("body")
            normalized["html_url"] = issue.get("html_url")
            normalized["label_names"] = _json_string_list_value(
                _extract_label_names(issue.get("labels"))
            )
    elif event_name == "check_run":
        check_run = payload.get("check_run")
        if isinstance(check_run, dict):
            normalized["check_conclusion"] = check_run.get("conclusion")
            normalized["head_sha"] = check_run.get("head_sha")
            normalized["html_url"] = check_run.get("html_url")
            normalized["title"] = check_run.get("name")
    elif event_name == "check_suite":
        check_suite = payload.get("check_suite")
        if isinstance(check_suite, dict):
            normalized["check_conclusion"] = check_suite.get("conclusion")
            normalized["head_sha"] = check_suite.get("head_sha")
            normalized["html_url"] = check_suite.get("url")
    if "label_names" not in normalized:
        normalized["label_names"] = _json_string_list_value(
            _extract_label_names(payload.get("labels"))
        )
    return normalized


def _extract_label_names(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    labels: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            labels.append(item.strip())
            continue
        if isinstance(item, dict):
            raw_name = item.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                labels.append(raw_name.strip())
    return labels


def _json_string_list_value(values: tuple[str, ...] | list[str]) -> list[JsonValue]:
    normalized: list[JsonValue] = []
    for value in values:
        normalized.append(value)
    return normalized


def _nested_text(payload: dict[str, JsonValue], *path: str) -> str | None:
    current: JsonValue = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, str) and current.strip():
        return current.strip()
    return None


def _validate_signature(*, body: bytes, signature: str, secret: str) -> bool:
    normalized_signature = signature.strip()
    if not normalized_signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    provided = normalized_signature.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)


def _render_template(template: str, context: dict[str, str]) -> str:
    class _SafeTemplateContext(dict[str, str]):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    return template.format_map(_SafeTemplateContext(context))


def _stringify_json_value(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=True)


def _json_text(value: JsonValue | None) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _json_identifier(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        return str(value)
    return None


def _json_bool(value: JsonValue | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _json_string_tuple(value: JsonValue | None) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            normalized.append(item.strip())
    return tuple(normalized)


def _parse_int(value: str | None) -> int | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _response_payload_dict(
    value: dict[str, JsonValue] | list[JsonValue],
) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return value
    return {"items": value}


def _resolve_failed_attempt_error(
    attempts: tuple[TriggerActionAttemptRecord, ...],
) -> str | None:
    for attempt in attempts:
        if attempt.status == TriggerActionStatus.FAILED:
            return attempt.last_error
    return None


def _build_dispatch_title(delivery: TriggerDeliveryRecord) -> str:
    action = f" {delivery.event_action}" if delivery.event_action else ""
    return f"GitHub {delivery.event_name}{action}".strip()


def _monitor_event_name_for_delivery(delivery: TriggerDeliveryRecord) -> str | None:
    event_name = delivery.event_name
    action = _normalize_optional_text(delivery.event_action) or ""
    if event_name == "pull_request":
        if action == "opened":
            return "pr.opened"
        if action in {"edited", "reopened", "synchronize"}:
            return "pr.updated"
        if action == "review_requested":
            return "pr.review_requested"
        return None
    if event_name == "issues":
        if action == "opened":
            return "issue.opened"
        if action in {"edited", "reopened"}:
            return "issue.updated"
        return None
    if event_name == "check_run" and action == "completed":
        return "check_run.completed"
    if event_name == "check_suite" and action == "completed":
        return "check_suite.completed"
    if event_name == "status":
        return "status.updated"
    return None


def _monitor_body_text_for_delivery(delivery: TriggerDeliveryRecord) -> str:
    payload = delivery.normalized_payload
    title = _normalize_optional_text(_json_text(payload.get("title"))) or ""
    conclusion = _normalize_optional_text(_json_text(payload.get("check_conclusion")))
    body = _normalize_optional_text(_json_text(payload.get("body"))) or ""
    if title and conclusion:
        return f"{title}: {conclusion}"
    if title:
        return title
    if body:
        return body
    return _build_dispatch_title(delivery)


def _monitor_attributes_from_normalized_payload(
    payload: dict[str, JsonValue],
) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for key, value in payload.items():
        normalized = _json_scalar_to_str(value)
        if normalized is not None and normalized:
            attributes[key] = normalized
    return attributes


def _json_scalar_to_str(value: JsonValue) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value)
    return None


__all__ = [
    "GitHubRepoSubscriptionConflictError",
    "GitHubTriggerAccountNameConflictError",
    "GitHubTriggerService",
    "TriggerRuleNameConflictError",
]
