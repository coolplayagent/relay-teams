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
    TriggerDeliveryIngestStatus,
    TriggerRepository,
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
    def get_repository(
        self, *, token: str, owner: str, repo: str
    ) -> dict[str, JsonValue]:
        assert token == "ghp_test"
        return {
            "id": 123456,
            "default_branch": "main",
            "full_name": f"{owner}/{repo}",
        }


class _FakeEventLog:
    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        _ = trace_id
        return ()


def _build_service(
    tmp_path: Path,
) -> tuple[GitHubTriggerService, TriggerRepository, _FakeGitHubTriggerSecretStore]:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True, exist_ok=True)
    repository = TriggerRepository(tmp_path / "triggers.db")
    secret_store = _FakeGitHubTriggerSecretStore()
    service = GitHubTriggerService(
        config_dir=config_dir,
        repository=repository,
        secret_store=secret_store,
        github_client=cast(GitHubApiClient, _FakeGitHubApiClient()),
        automation_service=cast(AutomationService, object()),
        session_service=cast(SessionService, object()),
        run_service=cast(RunManager, object()),
        run_runtime_repo=cast(RunRuntimeRepository, object()),
        event_log=_FakeEventLog(),
    )
    return service, repository, secret_store


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
    service, repository, _ = _build_service(tmp_path)
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
    service, _, secret_store = _build_service(tmp_path)
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
    service, repository, _ = _build_service(tmp_path)
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
