from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.interfaces.server.deps import get_wechat_gateway_service
from relay_teams.interfaces.server.routers import gateway
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.gateway.wechat import (
    WeChatAccountRecord,
    WeChatAccountStatus,
    WeChatAccountUpdateInput,
    WeChatLoginStartRequest,
    WeChatLoginStartResponse,
    WeChatLoginWaitRequest,
    WeChatLoginWaitResponse,
)


class _FakeWeChatGatewayService:
    def __init__(self) -> None:
        self.reload_calls = 0
        self.deleted_account_ids: list[str] = []
        self.updated_payloads: list[tuple[str, WeChatAccountUpdateInput]] = []

    def list_accounts(self) -> tuple[WeChatAccountRecord, ...]:
        return (self._record(),)

    def start_login(
        self,
        req: WeChatLoginStartRequest,
    ) -> WeChatLoginStartResponse:
        if req.bot_type == "bad":
            raise RuntimeError("bad request")
        return WeChatLoginStartResponse(
            session_key="wechat-login-1",
            qr_code_url="https://example.test/wechat-qr.png",
            message="Scan the QR code with WeChat to connect the account.",
        )

    def wait_login(
        self,
        req: WeChatLoginWaitRequest,
    ) -> WeChatLoginWaitResponse:
        if req.session_key == "missing":
            raise KeyError("Unknown WeChat login session: missing")
        return WeChatLoginWaitResponse(
            connected=True,
            account_id="wx_123",
            message="WeChat account connected.",
        )

    def update_account(
        self,
        account_id: str,
        req: WeChatAccountUpdateInput,
    ) -> WeChatAccountRecord:
        self.updated_payloads.append((account_id, req))
        if req.session_mode is not None and req.session_mode.value == "orchestration":
            if not req.orchestration_preset_id:
                raise ValueError("orchestration_preset_id is required")
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "display_name": req.display_name or "Updated Account",
                "workspace_id": req.workspace_id or "default",
                "session_mode": req.session_mode or self._record().session_mode,
                "orchestration_preset_id": req.orchestration_preset_id,
            }
        )

    def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> WeChatAccountRecord:
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "status": (
                    WeChatAccountStatus.ENABLED
                    if enabled
                    else WeChatAccountStatus.DISABLED
                ),
            }
        )

    def delete_account(self, account_id: str) -> None:
        if account_id == "missing":
            raise KeyError("Unknown account_id: missing")
        self.deleted_account_ids.append(account_id)

    def reload(self) -> None:
        self.reload_calls += 1

    @staticmethod
    def _record() -> WeChatAccountRecord:
        return WeChatAccountRecord(
            account_id="wx_123",
            display_name="WeChat Main",
            status=WeChatAccountStatus.ENABLED,
            workspace_id="default",
            thinking=RunThinkingConfig(),
            running=True,
            last_event_at=datetime(2026, 3, 26, 1, 0, tzinfo=UTC),
        )


def _client(fake_service: _FakeWeChatGatewayService) -> TestClient:
    app = FastAPI()
    app.include_router(gateway.router, prefix="/api")
    app.dependency_overrides[get_wechat_gateway_service] = lambda: fake_service
    return TestClient(app)


def test_list_wechat_accounts_route_returns_accounts() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.get("/api/gateway/wechat/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["account_id"] == "wx_123"
    assert payload[0]["running"] is True


def test_wait_wechat_login_route_maps_missing_session_to_404() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.post(
        "/api/gateway/wechat/login/wait",
        json={"session_key": "missing", "timeout_ms": 1000},
    )

    assert response.status_code == 404
    assert "Unknown WeChat login session" in response.json()["detail"]


def test_wait_wechat_login_route_rejects_none_like_session_key() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.post(
        "/api/gateway/wechat/login/wait",
        json={"session_key": "None", "timeout_ms": 1000},
    )

    assert response.status_code == 422


def test_update_wechat_account_route_maps_validation_error_to_422() -> None:
    fake_service = _FakeWeChatGatewayService()
    client = _client(fake_service)

    response = client.patch(
        "/api/gateway/wechat/accounts/wx_123",
        json={
            "display_name": "Ops WeChat",
            "workspace_id": "workspace-ops",
            "session_mode": "orchestration",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "orchestration_preset_id is required"


def test_update_wechat_account_route_allows_blank_route_tag_to_clear_value() -> None:
    fake_service = _FakeWeChatGatewayService()
    client = _client(fake_service)

    response = client.patch(
        "/api/gateway/wechat/accounts/wx_123",
        json={"route_tag": "   "},
    )

    assert response.status_code == 200
    assert fake_service.updated_payloads[0][1].route_tag is None
    assert "route_tag" in fake_service.updated_payloads[0][1].model_fields_set


def test_reload_wechat_gateway_route_calls_service() -> None:
    fake_service = _FakeWeChatGatewayService()
    client = _client(fake_service)

    response = client.post("/api/gateway/wechat/reload")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.reload_calls == 1


def test_update_wechat_account_route_rejects_none_like_path_identifier() -> None:
    fake_service = _FakeWeChatGatewayService()
    client = _client(fake_service)

    response = client.patch(
        "/api/gateway/wechat/accounts/None",
        json={"display_name": "Ops WeChat"},
    )

    assert response.status_code == 422
    assert fake_service.updated_payloads == []
