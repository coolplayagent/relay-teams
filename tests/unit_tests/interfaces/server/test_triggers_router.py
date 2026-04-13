from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import JsonValue

from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.interfaces.server.deps import (
    get_github_config_service,
    get_github_trigger_service,
)
from relay_teams.interfaces.server.routers import triggers
from relay_teams.triggers import (
    GitHubApiError,
    GitHubAvailableRepositoryRecord,
    GitHubRepoSubscriptionConflictError,
    GitHubRepoSubscriptionCreateInput,
    GitHubRepoSubscriptionRecord,
    GitHubRepoSubscriptionUpdateInput,
    GitHubTriggerAccountCreateInput,
    GitHubTriggerAccountNameConflictError,
    GitHubTriggerAccountRecord,
    GitHubTriggerAccountStatus,
    GitHubTriggerAccountUpdateInput,
    GitHubTriggerRunTemplate,
    GitHubWebhookStatus,
    TriggerDispatchConfig,
    TriggerProvider,
    TriggerRuleCreateInput,
    TriggerRuleMatchConfig,
    TriggerRuleNameConflictError,
    TriggerRuleRecord,
    TriggerRuleUpdateInput,
    TriggerTargetType,
)


class _FakeGitHubTriggerService:
    def __init__(self) -> None:
        self.delivery_calls: list[tuple[dict[str, str], bytes]] = []
        self.enabled_repo_ids: list[str] = []
        self.created_repo_requests: list[GitHubRepoSubscriptionCreateInput] = []

    def list_available_repositories(
        self,
        account_id: str,
        *,
        query: str | None = None,
    ) -> tuple[GitHubAvailableRepositoryRecord, ...]:
        if account_id != "ghta_1":
            raise KeyError(f"Unknown GitHub trigger account: {account_id}")
        if query == "forbidden":
            raise GitHubApiError(message="Forbidden", status_code=403)
        return (
            GitHubAvailableRepositoryRecord(
                owner="coolplayagent",
                repo_name="relay-teams",
                full_name="coolplayagent/relay-teams",
                default_branch="main",
                private=True,
            ),
        )

    def list_accounts(self) -> tuple[GitHubTriggerAccountRecord, ...]:
        return (self._account_record(),)

    def create_account(
        self,
        req: GitHubTriggerAccountCreateInput,
    ) -> GitHubTriggerAccountRecord:
        if req.name == "duplicate":
            raise GitHubTriggerAccountNameConflictError(
                f"GitHub trigger account name already exists: {req.name}"
            )
        return self._account_record()

    def update_account(
        self,
        account_id: str,
        _req: GitHubTriggerAccountUpdateInput,
    ) -> GitHubTriggerAccountRecord:
        if account_id != "ghta_1":
            raise KeyError(f"Unknown GitHub trigger account: {account_id}")
        return self._account_record()

    def delete_account(self, account_id: str) -> None:
        if account_id != "ghta_1":
            raise KeyError(f"Unknown GitHub trigger account: {account_id}")

    def enable_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        return self.update_account(account_id, GitHubTriggerAccountUpdateInput())

    def disable_account(self, account_id: str) -> GitHubTriggerAccountRecord:
        return self.update_account(account_id, GitHubTriggerAccountUpdateInput())

    def list_repo_subscriptions(self) -> tuple[GitHubRepoSubscriptionRecord, ...]:
        return (self._repo_record(),)

    def create_repo_subscription(
        self,
        req: GitHubRepoSubscriptionCreateInput,
    ) -> GitHubRepoSubscriptionRecord:
        self.created_repo_requests.append(req)
        if req.owner == "duplicate":
            raise GitHubRepoSubscriptionConflictError(
                f"Repository subscription already exists: {req.owner}/{req.repo_name}"
            )
        if req.owner == "missing":
            raise GitHubApiError(message="Not Found", status_code=404)
        if req.owner == "forbidden":
            raise GitHubApiError(message="Forbidden", status_code=403)
        if req.callback_url == "bad-url":
            raise ValueError("callback_url must be an absolute http or https URL")
        return self._repo_record()

    def update_repo_subscription(
        self,
        repo_subscription_id: str,
        req: GitHubRepoSubscriptionUpdateInput,
    ) -> GitHubRepoSubscriptionRecord:
        if repo_subscription_id != "ghrs_1":
            raise KeyError(f"Unknown GitHub repo subscription: {repo_subscription_id}")
        if req.callback_url == "bad-url":
            raise ValueError("callback_url must be an absolute http or https URL")
        return self._repo_record()

    def delete_repo_subscription(self, repo_subscription_id: str) -> None:
        if repo_subscription_id != "ghrs_1":
            raise KeyError(f"Unknown GitHub repo subscription: {repo_subscription_id}")

    def enable_repo_subscription(
        self,
        repo_subscription_id: str,
    ) -> GitHubRepoSubscriptionRecord:
        self.enabled_repo_ids.append(repo_subscription_id)
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

    def list_rules(self) -> tuple[TriggerRuleRecord, ...]:
        return (self._rule_record(),)

    def create_rule(self, req: TriggerRuleCreateInput) -> TriggerRuleRecord:
        if req.name == "duplicate":
            raise TriggerRuleNameConflictError(
                f"Trigger rule name already exists: {req.name}"
            )
        return self._rule_record()

    def update_rule(
        self,
        trigger_rule_id: str,
        _req: TriggerRuleUpdateInput,
    ) -> TriggerRuleRecord:
        if trigger_rule_id != "trg_1":
            raise KeyError(f"Unknown trigger rule: {trigger_rule_id}")
        return self._rule_record()

    def delete_rule(self, trigger_rule_id: str) -> None:
        if trigger_rule_id != "trg_1":
            raise KeyError(f"Unknown trigger rule: {trigger_rule_id}")

    def enable_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        return self.update_rule(trigger_rule_id, TriggerRuleUpdateInput(enabled=True))

    def disable_rule(self, trigger_rule_id: str) -> TriggerRuleRecord:
        return self.update_rule(trigger_rule_id, TriggerRuleUpdateInput(enabled=False))

    def handle_inbound_github_delivery(
        self,
        *,
        headers: dict[str, str],
        body: bytes,
    ) -> dict[str, JsonValue]:
        self.delivery_calls.append((headers, body))
        return {
            "trigger_delivery_id": "tdel_1",
            "ingest_status": "unmatched",
            "dispatch_count": 0,
        }

    @staticmethod
    def _account_record() -> GitHubTriggerAccountRecord:
        now = datetime(2026, 4, 13, 8, 0, tzinfo=UTC)
        return GitHubTriggerAccountRecord(
            account_id="ghta_1",
            name="primary",
            display_name="Primary",
            status=GitHubTriggerAccountStatus.ENABLED,
            token_configured=True,
            webhook_secret_configured=True,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _repo_record() -> GitHubRepoSubscriptionRecord:
        now = datetime(2026, 4, 13, 8, 0, tzinfo=UTC)
        return GitHubRepoSubscriptionRecord(
            repo_subscription_id="ghrs_1",
            account_id="ghta_1",
            owner="coolplayagent",
            repo_name="relay-teams",
            full_name="coolplayagent/relay-teams",
            callback_url="https://example.com/api/triggers/github/deliveries",
            subscribed_events=("pull_request",),
            webhook_status=GitHubWebhookStatus.REGISTERED,
            enabled=True,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _rule_record() -> TriggerRuleRecord:
        now = datetime(2026, 4, 13, 8, 0, tzinfo=UTC)
        return TriggerRuleRecord(
            trigger_rule_id="trg_1",
            provider=TriggerProvider.GITHUB,
            account_id="ghta_1",
            repo_subscription_id="ghrs_1",
            name="pr-opened",
            enabled=True,
            match_config=TriggerRuleMatchConfig(
                event_name="pull_request",
                actions=("opened",),
            ),
            dispatch_config=TriggerDispatchConfig(
                target_type=TriggerTargetType.RUN_TEMPLATE,
                run_template=GitHubTriggerRunTemplate(
                    workspace_id="default",
                    prompt_template="Investigate the delivery.",
                ),
            ),
            created_at=now,
            updated_at=now,
        )


class _FakeGitHubConfigService:
    def __init__(self, *, webhook_base_url: str | None = None) -> None:
        self._config = GitHubConfig(token=None, webhook_base_url=webhook_base_url)

    def get_github_config(self) -> GitHubConfig:
        return self._config


def _client(
    fake_service: _FakeGitHubTriggerService,
    *,
    webhook_base_url: str | None = None,
    base_url: str = "http://testserver",
) -> TestClient:
    app = FastAPI()
    app.include_router(triggers.router, prefix="/api")
    app.dependency_overrides[get_github_trigger_service] = lambda: fake_service
    app.dependency_overrides[get_github_config_service] = lambda: (
        _FakeGitHubConfigService(webhook_base_url=webhook_base_url)
    )
    return TestClient(app, base_url=base_url)


def test_list_github_accounts_route_returns_records() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.get("/api/triggers/github/accounts")

    assert response.status_code == 200
    assert response.json()[0]["account_id"] == "ghta_1"


def test_create_github_account_route_maps_name_conflict_to_409() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/accounts",
        json={
            "name": "duplicate",
            "display_name": "Duplicate",
            "token": "ghp_test",
            "webhook_secret": "whsec_test",
            "enabled": True,
        },
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_github_repo_route_maps_validation_error_to_422() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "coolplayagent",
            "repo_name": "relay-teams",
            "callback_url": "bad-url",
        },
    )

    assert response.status_code == 422
    assert "callback_url" in response.json()["detail"]


def test_list_github_available_repositories_route_returns_records() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.get(
        "/api/triggers/github/accounts/ghta_1/repositories",
        params={"query": "relay"},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "owner": "coolplayagent",
            "repo_name": "relay-teams",
            "full_name": "coolplayagent/relay-teams",
            "default_branch": "main",
            "private": True,
        }
    ]


def test_create_github_repo_route_auto_generates_callback_url_when_missing() -> None:
    fake_service = _FakeGitHubTriggerService()
    client = _client(
        fake_service,
        webhook_base_url="https://agent-teams.example.com/app",
    )

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "coolplayagent",
            "repo_name": "relay-teams",
        },
    )

    assert response.status_code == 200
    assert fake_service.created_repo_requests[0].callback_url == (
        "https://agent-teams.example.com/app/api/triggers/github/deliveries"
    )


def test_create_github_repo_route_leaves_callback_url_empty_for_local_request() -> None:
    fake_service = _FakeGitHubTriggerService()
    client = _client(fake_service, base_url="http://127.0.0.1:8000")

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "coolplayagent",
            "repo_name": "relay-teams",
        },
    )

    assert response.status_code == 200
    assert fake_service.created_repo_requests[0].callback_url is None


def test_create_github_repo_route_uses_public_request_url_when_base_url_missing() -> (
    None
):
    fake_service = _FakeGitHubTriggerService()
    client = _client(
        fake_service,
        webhook_base_url=None,
        base_url="https://agent-teams.example.com",
    )

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "coolplayagent",
            "repo_name": "relay-teams",
        },
    )

    assert response.status_code == 200
    assert fake_service.created_repo_requests[0].callback_url == (
        "https://agent-teams.example.com/api/triggers/github/deliveries"
    )


def test_create_github_repo_route_maps_provider_not_found_to_404() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "missing",
            "repo_name": "relay-teams",
            "callback_url": "https://example.com/hook",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Not Found"


def test_create_github_repo_route_maps_provider_error_to_422() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/repos",
        json={
            "account_id": "ghta_1",
            "owner": "forbidden",
            "repo_name": "relay-teams",
            "callback_url": "https://example.com/hook",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Forbidden"


def test_enable_github_repo_route_returns_record() -> None:
    fake_service = _FakeGitHubTriggerService()
    client = _client(fake_service)

    response = client.post("/api/triggers/github/repos/ghrs_1:enable")

    assert response.status_code == 200
    assert response.json()["repo_subscription_id"] == "ghrs_1"
    assert fake_service.enabled_repo_ids == ["ghrs_1"]


def test_update_github_rule_route_maps_missing_rule_to_404() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.patch(
        "/api/triggers/github/rules/trg_missing",
        json={"enabled": False},
    )

    assert response.status_code == 404


def test_create_github_rule_route_rejects_removed_match_fields() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/rules",
        json={
            "name": "pr-opened",
            "provider": "github",
            "account_id": "ghta_1",
            "repo_subscription_id": "ghrs_1",
            "match_config": {
                "event_name": "pull_request",
                "actions": ["opened"],
                "labels_any": ["bug"],
            },
            "dispatch_config": {
                "target_type": "run_template",
                "run_template": {
                    "workspace_id": "default",
                    "prompt_template": "Investigate the delivery.",
                },
            },
            "enabled": True,
        },
    )

    assert response.status_code == 422


def test_create_github_rule_route_rejects_removed_head_branch_filter() -> None:
    client = _client(_FakeGitHubTriggerService())

    response = client.post(
        "/api/triggers/github/rules",
        json={
            "name": "pr-opened",
            "provider": "github",
            "account_id": "ghta_1",
            "repo_subscription_id": "ghrs_1",
            "match_config": {
                "event_name": "pull_request",
                "actions": ["opened"],
                "head_branches": ["feature/demo"],
            },
            "dispatch_config": {
                "target_type": "run_template",
                "run_template": {
                    "workspace_id": "default",
                    "prompt_template": "Investigate the delivery.",
                },
            },
            "enabled": True,
        },
    )

    assert response.status_code == 422


def test_handle_github_delivery_route_passes_headers_and_body() -> None:
    fake_service = _FakeGitHubTriggerService()
    client = _client(fake_service)

    response = client.post(
        "/api/triggers/github/deliveries",
        headers={
            "X-GitHub-Delivery": "delivery-1",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=test",
        },
        content=b'{"repository":{"full_name":"coolplayagent/relay-teams"}}',
    )

    assert response.status_code == 200
    assert response.json()["trigger_delivery_id"] == "tdel_1"
    headers, body = fake_service.delivery_calls[0]
    assert headers["x-github-delivery"] == "delivery-1"
    assert headers["x-github-event"] == "pull_request"
    assert body == b'{"repository":{"full_name":"coolplayagent/relay-teams"}}'
