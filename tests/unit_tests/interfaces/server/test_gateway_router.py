from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from relay_teams.gateway.xiaoluban import (
    XiaolubanAccountCreateInput,
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanAccountUpdateInput,
    XiaolubanImConfigUpdateInput,
    XiaolubanInboundMessage,
    XiaolubanSecretStatus,
    XiaolubanTokenRevealResponse,
)
from relay_teams.interfaces.server.deps import (
    get_wechat_gateway_service,
    get_xiaoluban_gateway_service,
    get_xiaoluban_im_listener_service,
)
from relay_teams.interfaces.server.routers import gateway
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.gateway.wechat.models import (
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

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        _ = force
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

    async def list_accounts_async(self) -> object:
        return self.list_accounts()

    async def start_login_async(
        self, request: WeChatLoginStartRequest
    ) -> WeChatLoginStartResponse:
        return self.start_login(request)

    async def wait_login_async(
        self, request: WeChatLoginWaitRequest
    ) -> WeChatLoginWaitResponse:
        return self.wait_login(request)

    async def update_account_async(
        self, account_id: str, request: WeChatAccountUpdateInput
    ) -> WeChatAccountRecord:
        return self.update_account(account_id, request)

    async def set_account_enabled_async(self, account_id: str, enabled: bool) -> object:
        return self.set_account_enabled(account_id, enabled)

    async def delete_account_async(
        self, account_id: str, *, force: bool = False
    ) -> None:
        self.delete_account(account_id, force=force)

    async def reload_async(self) -> None:
        self.reload()


class _FakeXiaolubanGatewayService:
    def __init__(self) -> None:
        self.created_payloads: list[XiaolubanAccountCreateInput] = []
        self.updated_payloads: list[tuple[str, XiaolubanAccountUpdateInput]] = []
        self.updated_im_payloads: list[tuple[str, XiaolubanImConfigUpdateInput]] = []
        self.inbound_messages: list[tuple[str, XiaolubanInboundMessage]] = []
        self.deleted_account_ids: list[tuple[str, bool]] = []
        self.prepare_account_id_calls = 0
        self.revealed_account_ids: list[str] = []

    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]:
        return (self._record(),)

    def create_account(
        self,
        req: XiaolubanAccountCreateInput,
    ) -> XiaolubanAccountRecord:
        self.created_payloads.append(req)
        return self._record().model_copy(
            update={
                "account_id": req.account_id or self._record().account_id,
                "display_name": req.display_name,
            }
        )

    def update_account(
        self,
        account_id: str,
        req: XiaolubanAccountUpdateInput,
    ) -> XiaolubanAccountRecord:
        self.updated_payloads.append((account_id, req))
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "display_name": req.display_name or self._record().display_name,
            }
        )

    def update_im_config(
        self,
        account_id: str,
        req: XiaolubanImConfigUpdateInput,
    ) -> XiaolubanAccountRecord:
        self.updated_im_payloads.append((account_id, req))
        return self._record().model_copy(update={"account_id": account_id})

    def handle_im_inbound(
        self,
        *,
        account_id: str,
        message: XiaolubanInboundMessage,
    ) -> None:
        self.inbound_messages.append((account_id, message))

    def get_account(self, account_id: str) -> XiaolubanAccountRecord:
        return self._record().model_copy(update={"account_id": account_id})

    def get_im_callback_auth_token(self, account_id: str) -> str:
        del account_id
        return "secret-token"

    def prepare_account_id(self) -> str:
        self.prepare_account_id_calls += 1
        return "xlb_prepared"

    def reveal_token(self, account_id: str) -> XiaolubanTokenRevealResponse:
        self.revealed_account_ids.append(account_id)
        return XiaolubanTokenRevealResponse(token="uid_saved_token")

    def validate_im_workspace(self, workspace_id: str) -> None:
        del workspace_id

    def set_account_enabled(
        self,
        account_id: str,
        enabled: bool,
    ) -> XiaolubanAccountRecord:
        return self._record().model_copy(
            update={
                "account_id": account_id,
                "status": (
                    XiaolubanAccountStatus.ENABLED
                    if enabled
                    else XiaolubanAccountStatus.DISABLED
                ),
            }
        )

    def delete_account(self, account_id: str, *, force: bool = False) -> None:
        if account_id == "missing":
            raise KeyError("Unknown Xiaoluban account_id: missing")
        if account_id == "enabled" and not force:
            raise RuntimeError("Cannot delete enabled Xiaoluban account without force")
        self.deleted_account_ids.append((account_id, force))

    @staticmethod
    def _record() -> XiaolubanAccountRecord:
        from relay_teams.gateway.xiaoluban.models import XiaolubanImConfig

        return XiaolubanAccountRecord(
            account_id="xlb_123",
            display_name="小鲁班主账号",
            status=XiaolubanAccountStatus.ENABLED,
            derived_uid="uid_self",
            secret_status=XiaolubanSecretStatus(token_configured=True),
            im_config=XiaolubanImConfig(workspace_id="workspace-1"),
            created_at=datetime(2026, 4, 22, 1, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 22, 1, 0, tzinfo=UTC),
        )

    async def list_accounts_async(self) -> object:
        return self.list_accounts()

    async def create_account_async(
        self, request: XiaolubanAccountCreateInput
    ) -> XiaolubanAccountRecord:
        return self.create_account(request)

    async def update_account_async(
        self, account_id: str, request: XiaolubanAccountUpdateInput
    ) -> XiaolubanAccountRecord:
        return self.update_account(account_id, request)

    async def update_im_config_async(
        self, account_id: str, request: XiaolubanImConfigUpdateInput
    ) -> XiaolubanAccountRecord:
        return self.update_im_config(account_id, request)

    async def get_account_async(self, account_id: str) -> object:
        return self.get_account(account_id)

    async def get_im_callback_auth_token_async(self, account_id: str) -> str:
        return self.get_im_callback_auth_token(account_id)

    async def prepare_account_id_async(self) -> str:
        return self.prepare_account_id()

    async def reveal_token_async(self, account_id: str) -> object:
        return self.reveal_token(account_id)

    async def validate_im_workspace_async(self, workspace_id: str) -> None:
        self.validate_im_workspace(workspace_id)

    async def set_account_enabled_async(self, account_id: str, enabled: bool) -> object:
        return self.set_account_enabled(account_id, enabled)

    async def delete_account_async(
        self, account_id: str, *, force: bool = False
    ) -> None:
        self.delete_account(account_id, force=force)


class _FakeXiaolubanImListenerService:
    def __init__(
        self,
        *,
        running: bool = True,
        callback_host: str = "10.88.1.23",
        callback_port: int = 9009,
    ) -> None:
        self._running = running
        self._callback_host = callback_host
        self._callback_port = callback_port

    def is_running(self) -> bool:
        return self._running

    def callback_url(self, *, account_id: str) -> str:
        return (
            f"http://{self._callback_host}:{self._callback_port}/{account_id}"
            "?auth=secret-token"
        )


def _client(
    fake_service: _FakeWeChatGatewayService,
    fake_xiaoluban_service: _FakeXiaolubanGatewayService | None = None,
    *,
    base_url: str = "http://testserver",
    xiaoluban_im_listener: _FakeXiaolubanImListenerService | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(gateway.router, prefix="/api")
    app.dependency_overrides[get_wechat_gateway_service] = lambda: fake_service
    app.dependency_overrides[get_xiaoluban_gateway_service] = lambda: (
        fake_xiaoluban_service or _FakeXiaolubanGatewayService()
    )
    app.dependency_overrides[get_xiaoluban_im_listener_service] = lambda: (
        xiaoluban_im_listener or _FakeXiaolubanImListenerService()
    )
    return TestClient(app, base_url=base_url)


def test_list_wechat_accounts_route_returns_accounts() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.get("/api/gateway/wechat/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["account_id"] == "wx_123"
    assert payload[0]["running"] is True


def test_start_wechat_login_route_runs_service_call_in_threadpool() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.post(
        "/api/gateway/wechat/login/start",
        json={"bot_type": "lark"},
    )

    assert response.status_code == 200
    assert response.json()["session_key"] == "wechat-login-1"


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


def test_wait_wechat_login_route_runs_service_call_in_threadpool() -> None:
    client = _client(_FakeWeChatGatewayService())

    response = client.post(
        "/api/gateway/wechat/login/wait",
        json={"session_key": "wechat-login-1", "timeout_ms": 1000},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is True


def test_wechat_account_routes_run_service_calls_in_threadpool() -> None:
    fake_service = _FakeWeChatGatewayService()
    client = _client(fake_service)

    requests = [
        client.get("/api/gateway/wechat/accounts"),
        client.patch("/api/gateway/wechat/accounts/wx_123", json={"route_tag": "ops"}),
        client.post("/api/gateway/wechat/accounts/wx_123:enable"),
        client.post("/api/gateway/wechat/accounts/wx_123:disable"),
        client.delete("/api/gateway/wechat/accounts/wx_123"),
        client.post("/api/gateway/wechat/reload"),
    ]

    assert [response.status_code for response in requests] == [200] * len(requests)


def test_list_xiaoluban_accounts_route_returns_accounts() -> None:
    client = _client(
        _FakeWeChatGatewayService(),
        _FakeXiaolubanGatewayService(),
    )

    response = client.get("/api/gateway/xiaoluban/accounts")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["account_id"] == "xlb_123"
    assert payload[0]["derived_uid"] == "uid_self"


def test_create_xiaoluban_account_route_returns_created_record() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.post(
        "/api/gateway/xiaoluban/accounts",
        json={
            "display_name": "小鲁班主账号",
            "token": "uid_1234567890abcdef1234567890abcdef",
            "base_url": "http://xlb.test/send",
        },
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "小鲁班主账号"
    assert fake_xiaoluban_service.created_payloads[0].display_name == "小鲁班主账号"


def test_prepare_xiaoluban_account_route_returns_forwarding_command() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.post("/api/gateway/xiaoluban/accounts:prepare")

    assert response.status_code == 200
    assert response.json() == {
        "account_id": "xlb_prepared",
        "forwarding_url": "http://10.88.1.23:9009/xlb_prepared",
        "forwarding_command": "http://10.88.1.23:9009/xlb_prepared g",
        "listener_running": True,
    }
    assert fake_xiaoluban_service.prepare_account_id_calls == 1


def test_prepare_xiaoluban_account_route_maps_runtimeerror_to_409() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    listener = _FakeXiaolubanImListenerService()
    original_callback_url = listener.callback_url
    listener.callback_url = lambda *, account_id: (_ for _ in ()).throw(
        RuntimeError("xiaoluban_im_listener_host_unavailable")
    )
    client = _client(
        _FakeWeChatGatewayService(),
        fake_xiaoluban_service,
        xiaoluban_im_listener=listener,
    )

    response = client.post("/api/gateway/xiaoluban/accounts:prepare")

    assert response.status_code == 409
    listener.callback_url = original_callback_url


def test_reveal_xiaoluban_token_route_returns_saved_token() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.post("/api/gateway/xiaoluban/accounts/xlb_123:reveal-token")

    assert response.status_code == 200
    assert response.json() == {"token": "uid_saved_token"}
    assert fake_xiaoluban_service.revealed_account_ids == ["xlb_123"]


def test_reveal_xiaoluban_token_route_maps_keyerror_to_404() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    original_reveal = fake_xiaoluban_service.reveal_token
    fake_xiaoluban_service.reveal_token = lambda account_id: (_ for _ in ()).throw(
        KeyError("Unknown Xiaoluban account_id")
    )
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.post("/api/gateway/xiaoluban/accounts/missing:reveal-token")

    assert response.status_code == 404
    fake_xiaoluban_service.reveal_token = original_reveal


def test_disable_xiaoluban_account_route_returns_disabled_record() -> None:
    client = _client(
        _FakeWeChatGatewayService(),
        _FakeXiaolubanGatewayService(),
    )

    response = client.post("/api/gateway/xiaoluban/accounts/xlb_123:disable")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"


def test_delete_xiaoluban_account_route_forwards_force_flag() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.request(
        "DELETE",
        "/api/gateway/xiaoluban/accounts/enabled",
        json={"force": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_xiaoluban_service.deleted_account_ids == [("enabled", True)]


def test_xiaoluban_im_config_route_updates_settings() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.patch(
        "/api/gateway/xiaoluban/accounts/xlb_123/im",
        json={
            "workspace_id": "workspace-1",
        },
    )

    assert response.status_code == 200
    account_id, payload = fake_xiaoluban_service.updated_im_payloads[0]
    assert account_id == "xlb_123"
    assert payload.workspace_id == "workspace-1"


def test_xiaoluban_im_forwarding_command_route_uses_listener_url() -> None:
    client = _client(
        _FakeWeChatGatewayService(),
        _FakeXiaolubanGatewayService(),
        base_url="http://relay.test",
    )

    response = client.get(
        "/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"
    )

    assert response.status_code == 200
    assert response.json() == {
        "account_id": "xlb_123",
        "forwarding_url": "http://10.88.1.23:9009/xlb_123",
        "forwarding_command": "http://10.88.1.23:9009/xlb_123 g",
        "listener_running": True,
    }


def test_xiaoluban_im_routes_run_service_calls_in_threadpool() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    requests = [
        client.patch(
            "/api/gateway/xiaoluban/accounts/xlb_123/im",
            json={"workspace_id": "workspace-1"},
        ),
        client.get("/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"),
    ]

    assert [response.status_code for response in requests] == [200, 200]
    assert fake_xiaoluban_service.updated_im_payloads[0][0] == "xlb_123"
    assert (
        fake_xiaoluban_service.updated_im_payloads[0][1].workspace_id == "workspace-1"
    )


def test_xiaoluban_im_forwarding_command_reports_stopped_listener() -> None:
    client = _client(
        _FakeWeChatGatewayService(),
        _FakeXiaolubanGatewayService(),
        xiaoluban_im_listener=_FakeXiaolubanImListenerService(running=False),
    )

    response = client.get(
        "/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"
    )

    assert response.status_code == 200
    assert response.json()["forwarding_command"] == "http://10.88.1.23:9009/xlb_123 g"
    assert response.json()["listener_running"] is False


def test_xiaoluban_im_forwarding_command_route_uses_configured_listener_port() -> None:
    client = _client(
        _FakeWeChatGatewayService(),
        _FakeXiaolubanGatewayService(),
        xiaoluban_im_listener=_FakeXiaolubanImListenerService(
            callback_host="10.88.1.23",
            callback_port=8091,
        ),
    )

    response = client.get(
        "/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"
    )

    assert response.status_code == 200
    assert response.json()["forwarding_url"] == "http://10.88.1.23:8091/xlb_123"


def test_xiaoluban_account_routes_run_service_calls_in_threadpool() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    requests = [
        client.get("/api/gateway/xiaoluban/accounts"),
        client.post(
            "/api/gateway/xiaoluban/accounts",
            json={
                "display_name": "小鲁班主账号",
                "token": "uid_1234567890abcdef1234567890abcdef",
            },
        ),
        client.patch(
            "/api/gateway/xiaoluban/accounts/xlb_123",
            json={"display_name": "小鲁班备用账号"},
        ),
        client.post("/api/gateway/xiaoluban/accounts/xlb_123:enable"),
        client.post("/api/gateway/xiaoluban/accounts/xlb_123:disable"),
        client.delete("/api/gateway/xiaoluban/accounts/xlb_123"),
    ]

    assert [response.status_code for response in requests] == [200] * len(requests)


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


def test_update_xiaoluban_im_config_route_maps_valueerror_to_422() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    original_update = fake_xiaoluban_service.update_im_config
    fake_xiaoluban_service.update_im_config = lambda account_id, req: (
        _ for _ in ()
    ).throw(ValueError("workspace_id is required for Xiaoluban IM"))
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.patch(
        "/api/gateway/xiaoluban/accounts/xlb_123/im",
        json={"workspace_id": ""},
    )

    assert response.status_code == 422
    fake_xiaoluban_service.update_im_config = original_update


def test_xiaoluban_im_forwarding_command_route_maps_keyerror_to_404() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    original_get = fake_xiaoluban_service.get_account
    fake_xiaoluban_service.get_account = lambda account_id: (_ for _ in ()).throw(
        KeyError("Unknown")
    )
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.get(
        "/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"
    )

    assert response.status_code == 404
    fake_xiaoluban_service.get_account = original_get


def test_xiaoluban_im_forwarding_command_route_maps_runtimeerror_to_409() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    original_get = fake_xiaoluban_service.get_account
    fake_xiaoluban_service.get_account = lambda account_id: (_ for _ in ()).throw(
        RuntimeError("listener_host_unavailable")
    )
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.get(
        "/api/gateway/xiaoluban/accounts/xlb_123/im:forwarding-command"
    )

    assert response.status_code == 409
    fake_xiaoluban_service.get_account = original_get


def test_update_xiaoluban_im_config_route_maps_keyerror_to_404() -> None:
    fake_xiaoluban_service = _FakeXiaolubanGatewayService()
    original_update = fake_xiaoluban_service.update_im_config
    fake_xiaoluban_service.update_im_config = lambda account_id, req: (
        _ for _ in ()
    ).throw(KeyError("Unknown"))
    client = _client(_FakeWeChatGatewayService(), fake_xiaoluban_service)

    response = client.patch(
        "/api/gateway/xiaoluban/accounts/xlb_123/im",
        json={"workspace_id": "workspace-1"},
    )

    assert response.status_code == 404
    fake_xiaoluban_service.update_im_config = original_update
