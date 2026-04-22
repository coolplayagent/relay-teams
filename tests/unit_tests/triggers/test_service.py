# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Callable, cast

import pytest
from pydantic import JsonValue

from relay_teams.monitors import MonitorService
from relay_teams.automation.automation_service import AutomationService
from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.triggers import (
    GitHubActionSpec,
    GitHubActionType,
    GitHubApiClient,
    GitHubRepoSubscriptionCreateInput,
    GitHubRepoSubscriptionUpdateInput,
    GitHubTriggerAccountCreateInput,
    GitHubTriggerAccountUpdateInput,
    TriggerActionAttemptRecord,
    TriggerActionPhase,
    TriggerActionStatus,
    GitHubTriggerSecretStore,
    GitHubTriggerService,
    GitHubTriggerRunTemplate,
    GitHubWebhookStatus,
    TriggerDeliveryRecord,
    TriggerDispatchConfig,
    TriggerDispatchRecord,
    TriggerDispatchStatus,
    TriggerDeliveryIngestStatus,
    TriggerDeliverySignatureStatus,
    TriggerProvider,
    TriggerRepository,
    TriggerRuleCreateInput,
    TriggerRuleMatchConfig,
    TriggerTargetType,
)


class _FakeGitHubTriggerSecretStore(GitHubTriggerSecretStore):
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._webhook_secrets: dict[tuple[str, str], str] = {}

    def get_token(self, config_dir: Path, *, account_id: str) -> str | None:
        return self._tokens.get((str(config_dir.resolve()), account_id))

    def set_token(
        self, config_dir: Path, *, account_id: str, token: str | None
    ) -> None:
        key = (str(config_dir.resolve()), account_id)
        if token is None:
            self._tokens.pop(key, None)
            return
        self._tokens[key] = token

    def delete_token(self, config_dir: Path, *, account_id: str) -> None:
        self._tokens.pop((str(config_dir.resolve()), account_id), None)

    def get_webhook_secret(self, config_dir: Path, *, account_id: str) -> str | None:
        return self._webhook_secrets.get((str(config_dir.resolve()), account_id))

    def set_webhook_secret(
        self,
        config_dir: Path,
        *,
        account_id: str,
        webhook_secret: str | None,
    ) -> None:
        key = (str(config_dir.resolve()), account_id)
        if webhook_secret is None:
            self._webhook_secrets.pop(key, None)
            return
        self._webhook_secrets[key] = webhook_secret

    def delete_webhook_secret(self, config_dir: Path, *, account_id: str) -> None:
        self._webhook_secrets.pop((str(config_dir.resolve()), account_id), None)


class _FakeGitHubApiClient:
    def __init__(self) -> None:
        self.deleted_webhooks: list[tuple[str, str, str]] = []
        self.registered_webhooks: list[tuple[str, str, str, tuple[str, ...], str]] = []
        self.pull_request_files: tuple[str, ...] = ()
        self.available_repositories: tuple[dict[str, JsonValue], ...] = (
            {
                "id": 123456,
                "name": "relay-teams",
                "full_name": "coolplayagent/relay-teams",
                "default_branch": "main",
                "private": True,
                "owner": {"login": "coolplayagent"},
            },
        )

    def get_repository(
        self, *, token: str, owner: str, repo: str
    ) -> dict[str, JsonValue]:
        assert token == "ghp_test"
        return {
            "id": 123456,
            "default_branch": "main",
            "full_name": f"{owner}/{repo}",
        }

    def list_repositories(
        self,
        *,
        token: str,
        query: str | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        assert token == "ghp_test"
        if query is None:
            return self.available_repositories
        normalized_query = query.strip().lower()
        return tuple(
            payload
            for payload in self.available_repositories
            if normalized_query in str(payload.get("full_name", "")).lower()
        )

    def delete_repository_webhook(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        webhook_id: str,
    ) -> None:
        assert token == "ghp_test"
        self.deleted_webhooks.append((owner, repo, webhook_id))

    def register_repository_webhook(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        callback_url: str,
        webhook_secret: str,
        events: tuple[str, ...],
    ) -> dict[str, JsonValue]:
        assert token == "ghp_test"
        self.registered_webhooks.append(
            (owner, repo, callback_url, events, webhook_secret)
        )
        return {"id": f"hook-{len(self.registered_webhooks)}"}

    def list_pull_request_files(
        self,
        *,
        token: str,
        owner: str,
        repo: str,
        pull_request_number: int,
    ) -> tuple[str, ...]:
        assert token == "ghp_test"
        _ = (owner, repo, pull_request_number)
        return self.pull_request_files


class _FakeEventLog:
    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        _ = trace_id
        return ()


class _FakeMonitorService:
    def __init__(self) -> None:
        self.envelopes: list[object] = []

    def emit(self, envelope: object) -> tuple[object, ...]:
        self.envelopes.append(envelope)
        return ()


def _build_service(
    tmp_path: Path,
    *,
    monitor_service: _FakeMonitorService | None = None,
    get_github_config: Callable[[], GitHubConfig] | None = None,
) -> tuple[
    GitHubTriggerService,
    TriggerRepository,
    _FakeGitHubTriggerSecretStore,
    _FakeGitHubApiClient,
]:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True, exist_ok=True)
    repository = TriggerRepository(tmp_path / "triggers.db")
    secret_store = _FakeGitHubTriggerSecretStore()
    github_client = _FakeGitHubApiClient()
    service = GitHubTriggerService(
        config_dir=config_dir,
        repository=repository,
        secret_store=secret_store,
        github_client=cast(GitHubApiClient, github_client),
        automation_service=cast(AutomationService, object()),
        session_service=cast(SessionService, object()),
        run_service=cast(RunManager, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        event_log=_FakeEventLog(),
        monitor_service=cast(MonitorService | None, monitor_service),
        get_github_config=get_github_config,
    )
    return service, repository, secret_store, github_client


def _run_template() -> GitHubTriggerRunTemplate:
    return GitHubTriggerRunTemplate(
        workspace_id="default",
        prompt_template="Investigate the delivery.",
    )


def _create_account(service: GitHubTriggerService) -> str:
    account = service.create_account(
        GitHubTriggerAccountCreateInput(
            name="primary",
            display_name="Primary",
            token="ghp_test",
            webhook_secret="whsec_test",
            enabled=True,
        )
    )
    return account.account_id


def _build_signature(*, body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_create_repo_subscription_starts_unregistered_without_rules(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )

    persisted = repository.get_repo_subscription(created.repo_subscription_id)
    assert (
        persisted.callback_url == "https://example.com/api/triggers/github/deliveries"
    )
    assert persisted.webhook_status == GitHubWebhookStatus.UNREGISTERED
    assert persisted.subscribed_events == ()


def test_create_repo_subscription_uses_system_webhook_base_url_when_callback_missing(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(
        tmp_path,
        get_github_config=lambda: GitHubConfig(
            webhook_base_url="https://agent-teams.example.com/app",
        ),
    )
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url=None,
        )
    )

    persisted = repository.get_repo_subscription(created.repo_subscription_id)
    assert persisted.callback_url == (
        "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )
    assert persisted.webhook_status == GitHubWebhookStatus.UNREGISTERED


def test_update_account_clears_webhook_secret_when_explicitly_null(
    tmp_path: Path,
) -> None:
    service, _, secret_store, _ = _build_service(tmp_path)
    config_dir = tmp_path / ".agent-teams"
    account_id = _create_account(service)

    updated = service.update_account(
        account_id,
        GitHubTriggerAccountUpdateInput(clear_webhook_secret=True),
    )

    assert updated.webhook_secret_configured is False
    assert secret_store.get_webhook_secret(config_dir, account_id=account_id) is None


def test_handle_inbound_delivery_ignores_disabled_repo_subscriptions(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    _ = service.update_repo_subscription(
        created.repo_subscription_id,
        GitHubRepoSubscriptionUpdateInput(enabled=False),
    )
    body = json.dumps(
        {
            "action": "opened",
            "number": 318,
            "repository": {"full_name": "coolplayagent/relay-teams"},
            "pull_request": {"number": 318},
        }
    ).encode("utf-8")
    response = service.handle_inbound_github_delivery(
        headers={
            "x-github-delivery": "delivery-1",
            "x-github-event": "pull_request",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )
    delivery = repository.get_delivery(str(response["trigger_delivery_id"]))

    assert (
        response["ingest_status"] == TriggerDeliveryIngestStatus.INVALID_HEADERS.value
    )
    assert response["dispatch_count"] == 0
    assert delivery.repo_subscription_id is None
    assert delivery.account_id is None
    assert (
        delivery.last_error == "No repository subscription matched repository.full_name"
    )


def test_handle_inbound_delivery_ignores_disabled_trigger_accounts(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    _ = created
    _ = service.update_account(
        account_id,
        GitHubTriggerAccountUpdateInput(enabled=False),
    )
    body = json.dumps(
        {
            "action": "opened",
            "number": 318,
            "repository": {"full_name": "coolplayagent/relay-teams"},
            "pull_request": {"number": 318},
        }
    ).encode("utf-8")

    response = service.handle_inbound_github_delivery(
        headers={
            "x-github-delivery": "delivery-disabled-account",
            "x-github-event": "pull_request",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )
    delivery = repository.get_delivery(str(response["trigger_delivery_id"]))

    assert (
        response["ingest_status"] == TriggerDeliveryIngestStatus.INVALID_HEADERS.value
    )
    assert response["dispatch_count"] == 0
    assert delivery.repo_subscription_id is None
    assert delivery.account_id is None
    assert (
        delivery.last_error == "No repository subscription matched repository.full_name"
    )


def test_handle_inbound_issue_delivery_emits_issue_monitor_event(
    tmp_path: Path,
) -> None:
    monitor_service = _FakeMonitorService()
    service, repository, _, _ = _build_service(
        tmp_path,
        monitor_service=monitor_service,
    )
    account_id = _create_account(service)
    _ = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "coolplayagent/relay-teams"},
            "issue": {
                "number": 19,
                "title": "Webhook issue",
                "body": "Issue body",
                "html_url": "https://github.com/coolplayagent/relay-teams/issues/19",
                "labels": [{"name": "bug"}],
            },
            "sender": {"login": "octocat"},
        }
    ).encode("utf-8")

    response = service.handle_inbound_github_delivery(
        headers={
            "x-github-delivery": "delivery-issue-opened",
            "x-github-event": "issues",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )
    delivery = repository.get_delivery(str(response["trigger_delivery_id"]))

    assert response["ingest_status"] == TriggerDeliveryIngestStatus.UNMATCHED.value
    assert len(monitor_service.envelopes) == 1
    envelope = monitor_service.envelopes[0]
    assert getattr(envelope, "event_name") == "issue.opened"
    assert getattr(envelope, "source_key") == "coolplayagent/relay-teams"
    assert delivery.event_name == "issues"


def test_create_repo_subscription_uses_canonical_repository_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _, _, github_client = _build_service(tmp_path)
    account_id = _create_account(service)

    def _get_repository(*, token: str, owner: str, repo: str) -> dict[str, JsonValue]:
        assert token == "ghp_test"
        assert owner == "coolplayagent"
        assert repo == "relay-teams"
        return {
            "id": 123456,
            "default_branch": "main",
            "full_name": "CoolPlayAgent/Relay-Teams",
        }

    monkeypatch.setattr(github_client, "get_repository", _get_repository)

    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )

    assert created.owner == "CoolPlayAgent"
    assert created.repo_name == "Relay-Teams"
    assert created.full_name == "CoolPlayAgent/Relay-Teams"


def test_list_available_repositories_uses_effective_account_token(
    tmp_path: Path,
) -> None:
    service, _, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)

    repositories = service.list_available_repositories(account_id)

    assert len(repositories) == 1
    assert repositories[0].owner == "coolplayagent"
    assert repositories[0].repo_name == "relay-teams"
    assert repositories[0].full_name == "coolplayagent/relay-teams"
    assert repositories[0].default_branch == "main"
    assert repositories[0].private is True


def test_list_available_repositories_filters_query(
    tmp_path: Path,
) -> None:
    service, _, _, github_client = _build_service(tmp_path)
    account_id = _create_account(service)
    github_client.available_repositories = (
        {
            "id": 1,
            "name": "relay-teams",
            "full_name": "coolplayagent/relay-teams",
            "default_branch": "main",
            "private": True,
            "owner": {"login": "coolplayagent"},
        },
        {
            "id": 2,
            "name": "another-repo",
            "full_name": "coolplayagent/another-repo",
            "default_branch": "develop",
            "private": False,
            "owner": {"login": "coolplayagent"},
        },
    )

    repositories = service.list_available_repositories(account_id, query="relay")

    assert [repository.full_name for repository in repositories] == [
        "coolplayagent/relay-teams"
    ]


def test_create_rule_registers_webhook_with_derived_events(
    tmp_path: Path,
) -> None:
    service, repository, _, github_client = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )

    _ = service.create_rule(
        TriggerRuleCreateInput(
            name="pr-opened",
            provider=TriggerProvider.GITHUB,
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )

    persisted = repository.get_repo_subscription(created.repo_subscription_id)

    assert github_client.registered_webhooks == [
        (
            "coolplayagent",
            "relay-teams",
            "https://example.com/api/triggers/github/deliveries",
            ("pull_request",),
            "whsec_test",
        )
    ]
    assert persisted.subscribed_events == ("pull_request",)
    assert persisted.webhook_status == GitHubWebhookStatus.REGISTERED
    assert persisted.provider_webhook_id == "hook-1"


def test_disabling_last_rule_unregisters_repo_webhook(
    tmp_path: Path,
) -> None:
    service, repository, _, github_client = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    rule = service.create_rule(
        TriggerRuleCreateInput(
            name="pr-opened",
            provider=TriggerProvider.GITHUB,
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )

    _ = service.disable_rule(rule.trigger_rule_id)

    persisted = repository.get_repo_subscription(created.repo_subscription_id)

    assert persisted.subscribed_events == ()
    assert persisted.webhook_status == GitHubWebhookStatus.UNREGISTERED
    assert persisted.provider_webhook_id is None
    assert github_client.deleted_webhooks == [
        ("coolplayagent", "relay-teams", "hook-1")
    ]


def test_delete_repo_subscription_unregisters_remote_webhook(
    tmp_path: Path,
) -> None:
    service, repository, _, github_client = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    _ = repository.update_repo_subscription(
        created.model_copy(
            update={
                "provider_webhook_id": "42",
                "webhook_status": GitHubWebhookStatus.REGISTERED,
            }
        )
    )

    service.delete_repo_subscription(created.repo_subscription_id)

    assert github_client.deleted_webhooks == [("coolplayagent", "relay-teams", "42")]
    assert repository.list_repo_subscriptions() == ()


def test_delete_account_unregisters_repo_webhooks_before_cascade_delete(
    tmp_path: Path,
) -> None:
    service, repository, secret_store, github_client = _build_service(tmp_path)
    config_dir = tmp_path / ".agent-teams"
    account_id = _create_account(service)
    first = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    second = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams-docs",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    _ = repository.update_repo_subscription(
        first.model_copy(
            update={
                "provider_webhook_id": "42",
                "webhook_status": GitHubWebhookStatus.REGISTERED,
            }
        )
    )
    _ = repository.update_repo_subscription(
        second.model_copy(
            update={
                "provider_webhook_id": "43",
                "webhook_status": GitHubWebhookStatus.REGISTERED,
            }
        )
    )

    service.delete_account(account_id)

    assert sorted(github_client.deleted_webhooks) == [
        ("coolplayagent", "relay-teams", "42"),
        ("coolplayagent", "relay-teams-docs", "43"),
    ]
    assert repository.list_accounts() == ()
    assert repository.list_repo_subscriptions() == ()
    assert secret_store.get_token(config_dir, account_id=account_id) is None
    assert secret_store.get_webhook_secret(config_dir, account_id=account_id) is None


def test_repository_ignores_deprecated_rule_match_fields_in_persisted_json(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    repo = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    rule = service.create_rule(
        TriggerRuleCreateInput(
            name="compatibility-check",
            provider=TriggerProvider.GITHUB,
            account_id=account_id,
            repo_subscription_id=repo.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )
    repository._conn.execute(
        """
        UPDATE trigger_rules
        SET match_config_json=?
        WHERE trigger_rule_id=?
        """,
        (
            json.dumps(
                {
                    "event_name": "pull_request",
                    "actions": ["opened"],
                    "head_branches": ["feature/demo"],
                    "labels_any": ["bug"],
                    "sender_allow": ["octocat"],
                    "paths_any": ["src/**"],
                }
            ),
            rule.trigger_rule_id,
        ),
    )
    repository._conn.commit()

    persisted = repository.get_rule(rule.trigger_rule_id)

    assert persisted.match_config == TriggerRuleMatchConfig(
        event_name="pull_request",
        actions=("opened",),
    )


def test_handle_inbound_delivery_returns_duplicate_on_insert_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    body = json.dumps(
        {
            "action": "opened",
            "number": 318,
            "repository": {"full_name": "coolplayagent/relay-teams"},
            "pull_request": {"number": 318},
        }
    ).encode("utf-8")
    existing = repository.create_delivery(
        TriggerDeliveryRecord(
            trigger_delivery_id="tdel_existing",
            provider=TriggerProvider.GITHUB,
            provider_delivery_id="delivery-race",
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            event_name="pull_request",
            event_action="opened",
            signature_status=TriggerDeliverySignatureStatus.VALID,
            ingest_status=TriggerDeliveryIngestStatus.RECEIVED,
            headers={},
            payload={"repository": {"full_name": "coolplayagent/relay-teams"}},
            normalized_payload={"repository_full_name": "coolplayagent/relay-teams"},
        )
    )
    original_get_delivery = repository.get_delivery_by_provider_id
    lookup_calls = 0

    def _get_delivery_by_provider_id(
        *, provider: str, provider_delivery_id: str
    ) -> TriggerDeliveryRecord | None:
        nonlocal lookup_calls
        lookup_calls += 1
        if lookup_calls == 1:
            return None
        return original_get_delivery(
            provider=provider,
            provider_delivery_id=provider_delivery_id,
        )

    monkeypatch.setattr(
        repository,
        "get_delivery_by_provider_id",
        _get_delivery_by_provider_id,
    )

    response = service.handle_inbound_github_delivery(
        headers={
            "x-github-delivery": "delivery-race",
            "x-github-event": "pull_request",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )

    assert response == {
        "trigger_delivery_id": existing.trigger_delivery_id,
        "ingest_status": TriggerDeliveryIngestStatus.DUPLICATE.value,
        "dispatch_count": 0,
    }


def test_handle_inbound_delivery_preserves_rule_last_error_when_dispatch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    rule = service.create_rule(
        TriggerRuleCreateInput(
            name="dispatch-failure",
            provider=TriggerProvider.GITHUB,
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )
    monkeypatch.setattr(
        service,
        "_start_run_template",
        lambda **_: (_ for _ in ()).throw(RuntimeError("run template failed")),
    )
    body = json.dumps(
        {
            "action": "opened",
            "number": 318,
            "repository": {"full_name": "coolplayagent/relay-teams"},
            "pull_request": {"number": 318},
        }
    ).encode("utf-8")

    response = service.handle_inbound_github_delivery(
        headers={
            "x-github-delivery": "delivery-dispatch-failure",
            "x-github-event": "pull_request",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )

    dispatches = repository.list_dispatches_by_delivery(
        str(response["trigger_delivery_id"])
    )
    persisted_rule = repository.get_rule(rule.trigger_rule_id)

    assert response["ingest_status"] == TriggerDeliveryIngestStatus.TRIGGERED.value
    assert response["dispatch_count"] == 1
    assert len(dispatches) == 1
    assert dispatches[0].status == TriggerDispatchStatus.FAILED
    assert dispatches[0].last_error == "run template failed"
    assert persisted_rule.last_error == "run template failed"
    assert persisted_rule.last_fired_at is not None


def test_process_pending_actions_marks_attempt_failed_when_token_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
        )
    )
    _ = service.update_account(
        account_id,
        GitHubTriggerAccountUpdateInput(clear_token=True),
    )
    monkeypatch.setattr(service, "_get_system_github_token", lambda: None)
    monkeypatch.setattr(
        service,
        "_execute_action",
        lambda **_: (_ for _ in ()).throw(
            AssertionError("_execute_action should not run without a token")
        ),
    )
    delivery = repository.create_delivery(
        TriggerDeliveryRecord(
            trigger_delivery_id="tdel_pending",
            provider=TriggerProvider.GITHUB,
            provider_delivery_id="delivery-pending",
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            event_name="pull_request",
            event_action="opened",
            signature_status=TriggerDeliverySignatureStatus.VALID,
            ingest_status=TriggerDeliveryIngestStatus.TRIGGERED,
            headers={},
            payload={"repository": {"full_name": "coolplayagent/relay-teams"}},
            normalized_payload={
                "repository_full_name": "coolplayagent/relay-teams",
                "pull_request_number": 318,
            },
        )
    )
    dispatch = repository.create_dispatch(
        TriggerDispatchRecord(
            trigger_dispatch_id="tdis_pending",
            trigger_delivery_id=delivery.trigger_delivery_id,
            trigger_rule_id="trule_pending",
            target_type=TriggerTargetType.RUN_TEMPLATE,
            status=TriggerDispatchStatus.PENDING,
        )
    )
    _ = repository.create_action_attempt(
        TriggerActionAttemptRecord(
            trigger_action_attempt_id="tact_pending",
            trigger_dispatch_id=dispatch.trigger_dispatch_id,
            phase=TriggerActionPhase.IMMEDIATE,
            action_type=GitHubActionType.COMMENT,
            status=TriggerActionStatus.PENDING,
            action_spec=GitHubActionSpec(
                action_type=GitHubActionType.COMMENT,
                body_template="Investigating",
            ),
        )
    )

    assert service.process_pending_actions() is True

    attempt = repository.list_action_attempts_by_dispatch(dispatch.trigger_dispatch_id)[
        0
    ]
    assert attempt.status == TriggerActionStatus.FAILED
    assert attempt.attempt_count == 1
    assert attempt.last_error is not None
    assert "not configured" in attempt.last_error


def test_refresh_repo_callback_urls_from_system_config_replaces_local_callback(
    tmp_path: Path,
) -> None:
    service, _, _, github_client = _build_service(
        tmp_path,
        get_github_config=lambda: GitHubConfig(
            webhook_base_url="https://agent-teams.example.com/app",
        ),
    )
    account_id = _create_account(service)
    created = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="http://127.0.0.1:8000/api/triggers/github/deliveries",
        )
    )
    _ = service.create_rule(
        TriggerRuleCreateInput(
            name="issue-opened",
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="issues",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )

    updated = service.refresh_repo_callback_urls_from_system_config()

    assert len(updated) == 1
    assert updated[0].callback_url == (
        "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )
    assert updated[0].webhook_status == GitHubWebhookStatus.REGISTERED
    assert github_client.registered_webhooks[-1][2] == (
        "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )


def test_refresh_repo_callback_urls_from_system_config_clears_old_generated_callback(
    tmp_path: Path,
) -> None:
    service, _, _, _ = _build_service(
        tmp_path,
        get_github_config=lambda: GitHubConfig(webhook_base_url=None),
    )
    account_id = _create_account(service)
    _ = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams",
            callback_url="https://old.example.com/api/triggers/github/deliveries",
        )
    )

    updated = service.refresh_repo_callback_urls_from_system_config(
        previous_webhook_base_url="https://old.example.com",
    )

    assert len(updated) == 1
    assert updated[0].callback_url is None
    assert updated[0].webhook_status == GitHubWebhookStatus.UNREGISTERED
