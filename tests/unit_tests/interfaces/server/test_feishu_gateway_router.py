from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.gateway.feishu.errors import FeishuAccountNameConflictError
from relay_teams.gateway.feishu.models import (
    FeishuGatewayAccountCreateInput,
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
    FeishuGatewayAccountUpdateInput,
)
from relay_teams.interfaces.server.deps import (
    get_feishu_gateway_service,
    get_feishu_subscription_service,
)
from relay_teams.interfaces.server.routers import feishu_gateway


class _FakeFeishuGatewayService:
    def __init__(self) -> None:
        self.created_payloads: list[FeishuGatewayAccountCreateInput] = []
        self.updated_payloads: list[tuple[str, FeishuGatewayAccountUpdateInput]] = []
        self.deleted_account_ids: list[str] = []
        self.deleted_force_flags: list[bool] = []

    def list_accounts(self) -> tuple[FeishuGatewayAccountRecord, ...]:
        return (self._record(),)

    def create_account(
        self,
        payload: FeishuGatewayAccountCreateInput,
    ) -> FeishuGatewayAccountRecord:
        self.created_payloads.append(payload)
        if payload.name == "conflict":
            raise FeishuAccountNameConflictError("name conflict")
        return self._record().model_copy(update={"account_id": "fsg_created"})

    def get_account(self, account_id: str) -> FeishuGatewayAccountRecord:
        if account_id == "missing":
            raise KeyError("Unknown Feishu account: missing")
        return self._record().model_copy(update={"account_id": account_id})

    def subscription_runtime_changed_for_update(
        self,
        *,
        existing: FeishuGatewayAccountRecord,
        request: FeishuGatewayAccountUpdateInput,
    ) -> bool:
        _ = existing
        return request.source_config is not None

    def update_account(
        self,
        account_id: str,
        payload: FeishuGatewayAccountUpdateInput,
    ) -> FeishuGatewayAccountRecord:
        self.updated_payloads.append((account_id, payload))
        if payload.name == "conflict":
            raise FeishuAccountNameConflictError("name conflict")
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "name": payload.name or "feishu_main",
                "display_name": payload.display_name or "Feishu Main",
            }
        )

    def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> FeishuGatewayAccountRecord:
        if account_id == "invalid":
            raise ValueError("Unknown workspace: missing-workspace")
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "status": (
                    FeishuGatewayAccountStatus.ENABLED
                    if enabled
                    else FeishuGatewayAccountStatus.DISABLED
                ),
            }
        )

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        if account_id == "missing":
            raise KeyError("Unknown Feishu account: missing")
        self.deleted_account_ids.append(account_id)
        self.deleted_force_flags.append(force)

    @staticmethod
    def _record() -> FeishuGatewayAccountRecord:
        now = datetime(2026, 3, 26, 1, 0, tzinfo=UTC)
        return FeishuGatewayAccountRecord(
            account_id="fsg_main",
            name="feishu_main",
            display_name="Feishu Main",
            status=FeishuGatewayAccountStatus.ENABLED,
            source_config={
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
            target_config={"workspace_id": "default"},
            secret_config={"app_secret": "secret-demo"},
            secret_status={"app_secret_configured": True},
            created_at=now,
            updated_at=now,
        )


class _FakeSubscriptionService:
    def __init__(self) -> None:
        self.reload_calls = 0

    def reload(self) -> None:
        self.reload_calls += 1


def _client(
    gateway_service: _FakeFeishuGatewayService,
    subscription_service: _FakeSubscriptionService,
) -> TestClient:
    app = FastAPI()
    app.include_router(feishu_gateway.router, prefix="/api")
    app.dependency_overrides[get_feishu_gateway_service] = lambda: gateway_service
    app.dependency_overrides[get_feishu_subscription_service] = lambda: (
        subscription_service
    )
    return TestClient(app)


def test_list_feishu_accounts_route_returns_accounts() -> None:
    client = _client(_FakeFeishuGatewayService(), _FakeSubscriptionService())

    response = client.get("/api/gateway/feishu/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["account_id"] == "fsg_main"
    assert payload[0]["source_config"]["provider"] == "feishu"


def test_create_feishu_account_route_reloads_subscription_service() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.post(
        "/api/gateway/feishu/accounts",
        json={
            "name": "feishu_ops",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
            "target_config": {"workspace_id": "default"},
            "secret_config": {"app_secret": "secret-demo"},
            "enabled": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["account_id"] == "fsg_created"
    assert subscription_service.reload_calls == 1
    assert gateway_service.created_payloads[0].name == "feishu_ops"


def test_feishu_account_routes_run_service_calls_in_threadpool(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_run_in_threadpool(
        func: Callable[[], object],
    ) -> object:
        calls.append(func.__name__)
        return func()

    monkeypatch.setattr(feishu_gateway, "run_in_threadpool", fake_run_in_threadpool)
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    requests = [
        client.get("/api/gateway/feishu/accounts"),
        client.post(
            "/api/gateway/feishu/accounts",
            json={
                "name": "feishu_ops",
                "source_config": {
                    "provider": "feishu",
                    "trigger_rule": "mention_only",
                    "app_id": "cli_demo",
                    "app_name": "Agent Teams Bot",
                },
                "target_config": {"workspace_id": "default"},
                "secret_config": {"app_secret": "secret-demo"},
            },
        ),
        client.patch(
            "/api/gateway/feishu/accounts/fsg_main",
            json={
                "source_config": {
                    "provider": "feishu",
                    "trigger_rule": "all_messages",
                    "app_id": "cli_demo",
                    "app_name": "Agent Teams Bot",
                },
            },
        ),
        client.post("/api/gateway/feishu/accounts/fsg_main:enable"),
        client.post("/api/gateway/feishu/accounts/fsg_main:disable"),
        client.delete("/api/gateway/feishu/accounts/fsg_main"),
        client.post("/api/gateway/feishu/reload"),
    ]

    assert [response.status_code for response in requests] == [200] * len(requests)
    assert calls == [
        "list_accounts",
        "_create_feishu_account",
        "_update_feishu_account",
        "_enable_feishu_account",
        "_disable_feishu_account",
        "_delete_feishu_account",
        "reload",
    ]


def test_create_feishu_account_route_rejects_none_like_name() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.post(
        "/api/gateway/feishu/accounts",
        json={
            "name": "None",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
            "target_config": {"workspace_id": "default"},
            "secret_config": {"app_secret": "secret-demo"},
        },
    )

    assert response.status_code == 422
    assert gateway_service.created_payloads == []


def test_create_feishu_account_route_rejects_unknown_nested_config_field() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.post(
        "/api/gateway/feishu/accounts",
        json={
            "name": "feishu_ops",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "mention_only",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
            "target_config": {"workspace_id": "default", "unknown_field": True},
            "secret_config": {"app_secret": "secret-demo"},
        },
    )

    assert response.status_code == 422
    assert gateway_service.created_payloads == []


def test_update_feishu_account_route_reloads_when_runtime_changes() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.patch(
        "/api/gateway/feishu/accounts/fsg_main",
        json={
            "name": "feishu_ops",
            "source_config": {
                "provider": "feishu",
                "trigger_rule": "all_messages",
                "app_id": "cli_demo",
                "app_name": "Agent Teams Bot",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "feishu_ops"
    assert subscription_service.reload_calls == 1
    assert gateway_service.updated_payloads[0][0] == "fsg_main"


def test_update_feishu_account_route_rejects_empty_typed_config_patch() -> None:
    client = _client(_FakeFeishuGatewayService(), _FakeSubscriptionService())

    response = client.patch(
        "/api/gateway/feishu/accounts/fsg_main",
        json={"target_config": {}},
    )

    assert response.status_code == 422
    assert "config patch must not be empty" in response.text


def test_delete_feishu_account_route_maps_missing_account_to_404() -> None:
    client = _client(_FakeFeishuGatewayService(), _FakeSubscriptionService())

    response = client.delete("/api/gateway/feishu/accounts/missing")

    assert response.status_code == 404
    assert "Unknown Feishu account" in response.json()["detail"]


def test_delete_feishu_account_route_forwards_force_flag() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.request(
        "DELETE",
        "/api/gateway/feishu/accounts/fsg_main",
        json={"force": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert gateway_service.deleted_account_ids == ["fsg_main"]
    assert gateway_service.deleted_force_flags == [True]
    assert subscription_service.reload_calls == 1


def test_enable_feishu_account_route_maps_validation_error_to_422() -> None:
    client = _client(_FakeFeishuGatewayService(), _FakeSubscriptionService())

    response = client.post("/api/gateway/feishu/accounts/invalid:enable")

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown workspace: missing-workspace"


def test_update_feishu_account_route_rejects_none_like_path_identifier() -> None:
    gateway_service = _FakeFeishuGatewayService()
    subscription_service = _FakeSubscriptionService()
    client = _client(gateway_service, subscription_service)

    response = client.patch(
        "/api/gateway/feishu/accounts/None",
        json={"display_name": "Feishu Ops"},
    )

    assert response.status_code == 422
    assert gateway_service.updated_payloads == []
