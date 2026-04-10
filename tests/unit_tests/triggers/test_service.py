# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from relay_teams.automation import AutomationService
from relay_teams.sessions.runs.run_manager import RunManager
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.triggers import (
    GitHubApiClient,
    GitHubRepoSubscriptionCreateInput,
    GitHubRepoSubscriptionUpdateInput,
    GitHubTriggerAccountCreateInput,
    GitHubTriggerAccountUpdateInput,
    GitHubTriggerSecretStore,
    GitHubTriggerService,
    GitHubTriggerRunTemplate,
    GitHubWebhookStatus,
    TriggerDispatchConfig,
    TriggerDeliveryIngestStatus,
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
        self.pull_request_files: tuple[str, ...] = ()

    def get_repository(
        self, *, token: str, owner: str, repo: str
    ) -> dict[str, JsonValue]:
        assert token == "ghp_test"
        return {
            "id": 123456,
            "default_branch": "main",
            "full_name": f"{owner}/{repo}",
        }

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


def _build_service(
    tmp_path: Path,
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


def test_create_repo_subscription_requires_callback_before_persisting(
    tmp_path: Path,
) -> None:
    service, repository, _, _ = _build_service(tmp_path)
    account_id = _create_account(service)

    with pytest.raises(
        ValueError, match="callback_url is required when register_webhook=true"
    ):
        service.create_repo_subscription(
            GitHubRepoSubscriptionCreateInput(
                account_id=account_id,
                owner="coolplayagent",
                repo_name="relay-teams",
                subscribed_events=("pull_request",),
                register_webhook=True,
                callback_url=None,
            )
        )

    assert repository.list_repo_subscriptions() == ()


def test_update_account_clears_webhook_secret_when_explicitly_null(
    tmp_path: Path,
) -> None:
    service, _, secret_store, _ = _build_service(tmp_path)
    config_dir = tmp_path / ".agent-teams"
    account_id = _create_account(service)

    updated = service.update_account(
        account_id,
        GitHubTriggerAccountUpdateInput(webhook_secret=None),
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
            subscribed_events=("pull_request",),
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
            subscribed_events=("pull_request",),
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
            subscribed_events=("pull_request",),
        )
    )
    second = service.create_repo_subscription(
        GitHubRepoSubscriptionCreateInput(
            account_id=account_id,
            owner="coolplayagent",
            repo_name="relay-teams-docs",
            subscribed_events=("pull_request",),
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


def test_handle_inbound_delivery_records_non_match_when_paths_need_missing_token(
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
            subscribed_events=("pull_request",),
        )
    )
    _ = service.create_rule(
        TriggerRuleCreateInput(
            name="paths-match",
            provider=TriggerProvider.GITHUB,
            account_id=account_id,
            repo_subscription_id=created.repo_subscription_id,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
                paths_any=("src/**/*.py",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=_run_template(),
            ),
        )
    )
    _ = service.update_account(
        account_id,
        GitHubTriggerAccountUpdateInput(token=None),
    )
    monkeypatch.setattr(service, "_get_system_github_token", lambda: None)
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
            "x-github-delivery": "delivery-paths-no-token",
            "x-github-event": "pull_request",
            "x-hub-signature-256": _build_signature(body=body, secret="whsec_test"),
        },
        body=body,
    )

    evaluations = repository.list_evaluations_by_delivery(
        str(response["trigger_delivery_id"])
    )
    assert response["ingest_status"] == TriggerDeliveryIngestStatus.UNMATCHED.value
    assert response["dispatch_count"] == 0
    assert len(evaluations) == 1
    assert evaluations[0].matched is False
    assert evaluations[0].reason_code == "changed_files_unavailable"
