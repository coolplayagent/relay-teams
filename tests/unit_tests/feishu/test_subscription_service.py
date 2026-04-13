# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ssl
import sys
from types import ModuleType, SimpleNamespace
from typing import cast
import warnings

import httpx
import pytest
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedOK, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.gateway.feishu.lark_ws_compat import (
    import_lark_module,
    import_lark_ws_client_module,
)
from relay_teams.gateway.feishu.models import (
    FeishuEnvironment,
    FeishuTriggerRuntimeConfig,
    FeishuTriggerSourceConfig,
    FeishuTriggerTargetConfig,
    TriggerProcessingResult,
)
from relay_teams.gateway.feishu.subscription_service import (
    FeishuSubscriptionService,
    WsClientLike,
    _FeishuWsController,
    _FeishuWsHub,
    _build_websocket_ssl_context,
    _parse_ws_conn_exception,
    _resolve_websocket_proxy_url,
)


def _build_runtime(
    *,
    trigger_id: str,
    name: str,
    app_id: str,
    app_name: str,
    app_secret: str,
) -> FeishuTriggerRuntimeConfig:
    return FeishuTriggerRuntimeConfig(
        trigger_id=trigger_id,
        trigger_name=name,
        source=FeishuTriggerSourceConfig(
            provider="feishu",
            trigger_rule="mention_only",
            app_id=app_id,
            app_name=app_name,
        ),
        target=FeishuTriggerTargetConfig(workspace_id="default"),
        environment=FeishuEnvironment(
            app_id=app_id,
            app_secret=app_secret,
            app_name=app_name,
        ),
    )


class _FakeRuntimeConfigLookup:
    def __init__(self, runtime_configs: tuple[FeishuTriggerRuntimeConfig, ...]) -> None:
        self.runtime_configs = runtime_configs

    def list_enabled_runtime_configs(self) -> tuple[FeishuTriggerRuntimeConfig, ...]:
        return self.runtime_configs


class _FakeRunner:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_alive(self) -> bool:
        return self.started and not self.stopped


class _FakeHandler:
    def handle_sdk_event(self, **_kwargs: object) -> TriggerProcessingResult:
        return TriggerProcessingResult(
            status="ignored",
            trigger_id="trg_test",
            ignored=True,
            reason="test",
        )


class _FakeShutdownableRunnerFactory:
    def __init__(self, runner: _FakeRunner) -> None:
        self._runner = runner
        self.shutdown_calls = 0

    def __call__(self, **_kwargs: object) -> _FakeRunner:
        return self._runner

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class _FakeAsyncController:
    def __init__(self, *, trigger_id: str) -> None:
        self.trigger_id = trigger_id
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        await asyncio.sleep(0)
        self.started = True
        self.stopped = False

    async def stop(self) -> None:
        await asyncio.sleep(0)
        self.stopped = True

    def is_running(self) -> bool:
        return self.started and not self.stopped


def test_subscription_service_starts_one_runner_per_enabled_bot() -> None:
    runtime_a = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-a",
    )
    runtime_b = _build_runtime(
        trigger_id="trg_b",
        name="bot_b",
        app_id="cli_b",
        app_name="bot-b",
        app_secret="secret-b",
    )
    runner_a = _FakeRunner()
    runner_b = _FakeRunner()
    runners = [runner_a, runner_b]
    service = FeishuSubscriptionService(
        runtime_config_lookup=_FakeRuntimeConfigLookup((runtime_a, runtime_b)),
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: runners.pop(0),
    )

    service.start()

    assert runner_a.started is True
    assert runner_b.started is True
    assert runner_a.stopped is False
    assert runner_b.stopped is False


def test_subscription_service_reloads_only_changed_bot_runner() -> None:
    first_runtime = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-a",
    )
    second_runtime = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-b",
    )
    first_runner = _FakeRunner()
    second_runner = _FakeRunner()
    lookup = _FakeRuntimeConfigLookup((first_runtime,))
    service = FeishuSubscriptionService(
        runtime_config_lookup=lookup,
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: first_runner,
    )

    service.start()
    lookup.runtime_configs = (second_runtime,)
    service._runner_factory = lambda **_kwargs: second_runner
    service.reload()

    assert first_runner.started is True
    assert first_runner.stopped is True
    assert second_runner.started is True
    assert second_runner.stopped is False


def test_subscription_service_stops_runner_when_bot_no_longer_enabled() -> None:
    runtime = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-a",
    )
    runner = _FakeRunner()
    lookup = _FakeRuntimeConfigLookup((runtime,))
    service = FeishuSubscriptionService(
        runtime_config_lookup=lookup,
        event_handler=_FakeHandler(),
        runner_factory=lambda **_kwargs: runner,
    )

    service.start()
    lookup.runtime_configs = ()
    service.reload()

    assert runner.started is True
    assert runner.stopped is True


def test_subscription_service_stop_shuts_down_shared_runner_factory() -> None:
    runtime = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-a",
    )
    runner = _FakeRunner()
    runner_factory = _FakeShutdownableRunnerFactory(runner)
    service = FeishuSubscriptionService(
        runtime_config_lookup=_FakeRuntimeConfigLookup((runtime,)),
        event_handler=_FakeHandler(),
        runner_factory=runner_factory,
    )

    service.start()
    service.stop()

    assert runner.started is True
    assert runner.stopped is True
    assert runner_factory.shutdown_calls == 1


def test_feishu_ws_hub_reuses_single_thread_for_multiple_bots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_a = _build_runtime(
        trigger_id="trg_a",
        name="bot_a",
        app_id="cli_a",
        app_name="bot-a",
        app_secret="secret-a",
    )
    runtime_b = _build_runtime(
        trigger_id="trg_b",
        name="bot_b",
        app_id="cli_b",
        app_name="bot-b",
        app_secret="secret-b",
    )
    created_controllers: dict[str, _FakeAsyncController] = {}

    def _controller_factory(
        *,
        runtime_config: FeishuTriggerRuntimeConfig,
        event_handler: object,
    ) -> _FakeAsyncController:
        _ = event_handler
        controller = _FakeAsyncController(trigger_id=runtime_config.trigger_id)
        created_controllers[runtime_config.trigger_id] = controller
        return controller

    monkeypatch.setattr(
        "relay_teams.gateway.feishu.subscription_service.import_lark_ws_client_module",
        lambda: SimpleNamespace(),
    )
    hub = _FeishuWsHub(controller_factory=_controller_factory)

    hub.start_client(runtime_config=runtime_a, event_handler=_FakeHandler())
    thread = hub._thread
    hub.start_client(runtime_config=runtime_b, event_handler=_FakeHandler())

    assert thread is not None
    assert hub._thread is thread
    assert hub.is_client_active("trg_a") is True
    assert hub.is_client_active("trg_b") is True
    assert created_controllers["trg_a"].started is True
    assert created_controllers["trg_b"].started is True

    hub.stop_client("trg_a")

    assert hub.is_client_active("trg_a") is False
    assert hub.is_client_active("trg_b") is True
    assert created_controllers["trg_a"].stopped is True
    assert created_controllers["trg_b"].stopped is False

    hub.shutdown()

    assert created_controllers["trg_b"].stopped is True
    assert hub._thread is None


class _FakeWsConnection:
    async def close(self) -> None:
        return None

    async def recv(self) -> bytes | str:
        return b""


class _FakeEndpointClient:
    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, str],
    ) -> httpx.Response:
        self.requests.append((url, headers, json))
        return self.response


class _FakeWsClient:
    def __init__(self) -> None:
        self._app_id = "cli_demo"
        self._app_secret = "secret-demo"
        self._auto_reconnect = True
        self._ping_interval = 120
        self._reconnect_count = -1
        self._reconnect_interval = 120
        self._reconnect_nonce = 30
        self._conn: _FakeWsConnection | None = None
        self._conn_url = ""
        self._service_id = ""
        self._conn_id = ""
        self.configured_ping_interval: int | None = None

    async def _disconnect(self) -> None:
        self._conn = None

    async def _handle_message(self, msg: bytes) -> None:
        _ = msg

    async def _write_message(self, data: bytes) -> None:
        _ = data

    def _fmt_log(self, fmt: str, *args: object) -> str:
        return fmt.format(*args)

    def _get_conn_url(self) -> str:
        raise AssertionError("controller should not call SDK _get_conn_url directly")

    def _configure(self, conf: object) -> None:
        ping_interval = getattr(conf, "PingInterval", None)
        if isinstance(ping_interval, int):
            self.configured_ping_interval = ping_interval


def test_feishu_ws_controller_get_conn_url_uses_net_http_client(monkeypatch) -> None:
    controller = _FeishuWsController(
        runtime_config=_build_runtime(
            trigger_id="trg_a",
            name="bot_a",
            app_id="cli_demo",
            app_name="bot-a",
            app_secret="secret-demo",
        ),
        event_handler=_FakeHandler(),
    )
    fake_http_client = _FakeEndpointClient(
        httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "ok",
                "data": {
                    "URL": "wss://open.feishu.cn/ws?device_id=device-1&service_id=7",
                    "ClientConfig": {"PingInterval": 45},
                },
            },
            request=httpx.Request(
                "POST", "https://open.feishu.cn/callback/ws/endpoint"
            ),
        )
    )
    monkeypatch.setattr(
        controller,
        "_create_feishu_http_client",
        lambda: fake_http_client,
    )
    const_module = ModuleType("lark_oapi.ws.const")
    setattr(const_module, "GEN_ENDPOINT_URI", "/callback/ws/endpoint")
    exception_module = ModuleType("lark_oapi.ws.exception")

    class _FakeClientException(Exception):
        def __init__(self, code: int, message: str) -> None:
            super().__init__(message)
            self.code = code
            self.message = message

    class _FakeServerException(Exception):
        def __init__(self, code: int, message: str) -> None:
            super().__init__(message)
            self.code = code
            self.message = message

    setattr(exception_module, "ClientException", _FakeClientException)
    setattr(exception_module, "ServerException", _FakeServerException)
    model_module = ModuleType("lark_oapi.ws.model")

    class _FakeEndpointResp:
        def __init__(self, payload: dict[str, object]) -> None:
            data = cast(dict[str, object], payload["data"])
            client_config = cast(dict[str, object], data["ClientConfig"])
            self.code = payload["code"]
            self.msg = payload["msg"]
            self.data = SimpleNamespace(
                URL=data["URL"],
                ClientConfig=SimpleNamespace(
                    PingInterval=client_config["PingInterval"]
                ),
            )

    setattr(model_module, "EndpointResp", _FakeEndpointResp)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.const", const_module)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.exception", exception_module)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.model", model_module)
    ws_client = _FakeWsClient()

    conn_url = controller._get_conn_url(cast(WsClientLike, ws_client))

    assert conn_url == "wss://open.feishu.cn/ws?device_id=device-1&service_id=7"
    assert fake_http_client.requests == [
        (
            "https://open.feishu.cn/callback/ws/endpoint",
            {"locale": "zh"},
            {"AppID": "cli_demo", "AppSecret": "secret-demo"},
        )
    ]
    assert ws_client.configured_ping_interval == 45


def test_build_websocket_ssl_context_respects_proxy_ssl_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.feishu.subscription_service.load_proxy_env_config",
        lambda: ProxyEnvConfig(ssl_verify=False),
    )

    ssl_context = _build_websocket_ssl_context("wss://open.feishu.cn/ws")

    assert ssl_context is not None
    assert ssl_context.verify_mode == ssl.CERT_NONE
    assert ssl_context.check_hostname is False


def test_resolve_websocket_proxy_url_uses_https_proxy_for_wss(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.feishu.subscription_service.load_proxy_env_config",
        lambda: ProxyEnvConfig(
            https_proxy="http://proxy.internal:8443",
            http_proxy="http://proxy.internal:8080",
            no_proxy="localhost,127.0.0.1",
        ),
    )

    proxy_url = _resolve_websocket_proxy_url(
        "wss://open.feishu.cn/ws?device_id=1&service_id=2"
    )

    assert proxy_url == "http://proxy.internal:8443"


def test_resolve_websocket_proxy_url_respects_no_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        "relay_teams.gateway.feishu.subscription_service.load_proxy_env_config",
        lambda: ProxyEnvConfig(
            https_proxy="http://proxy.internal:8443",
            no_proxy="open.feishu.cn",
        ),
    )

    proxy_url = _resolve_websocket_proxy_url(
        "wss://open.feishu.cn/ws?device_id=1&service_id=2"
    )

    assert proxy_url is None


def test_import_lark_ws_client_module_suppresses_known_deprecations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        previous_loop = asyncio.get_running_loop()
    except RuntimeError:
        previous_loop = None

    loop = asyncio.new_event_loop()

    try:
        asyncio.set_event_loop(loop)

        def _fake_import(module_name: str) -> ModuleType:
            warnings.warn_explicit(
                "datetime.datetime.utcfromtimestamp() is deprecated",
                DeprecationWarning,
                filename="well_known_types.py",
                lineno=1,
                module="lark_oapi.ws.pb.google.protobuf.internal.well_known_types",
            )
            warnings.warn_explicit(
                "There is no current event loop",
                DeprecationWarning,
                filename="client.py",
                lineno=1,
                module="lark_oapi.ws.client",
            )
            warnings.warn_explicit(
                "websockets.InvalidStatusCode is deprecated",
                DeprecationWarning,
                filename="client.py",
                lineno=1,
                module="lark_oapi.ws.client",
            )
            warnings.warn_explicit(
                "websockets.legacy is deprecated",
                DeprecationWarning,
                filename="legacy.py",
                lineno=1,
                module="websockets.legacy.client",
            )
            return ModuleType(module_name)

        monkeypatch.setattr(
            "relay_teams.gateway.feishu.lark_ws_compat.importlib.import_module",
            _fake_import,
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("default")
            module = import_lark_ws_client_module()
        assert module.__name__ == "lark_oapi.ws.client"
        assert caught == []
    finally:
        loop.close()
        asyncio.set_event_loop(previous_loop)


def test_import_lark_module_suppresses_dispatcher_handler_deprecations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_import(module_name: str) -> ModuleType:
        warnings.warn_explicit(
            "datetime.datetime.utcfromtimestamp() is deprecated",
            DeprecationWarning,
            filename="well_known_types.py",
            lineno=1,
            module="lark_oapi.ws.pb.google.protobuf.internal.well_known_types",
        )
        warnings.warn_explicit(
            "websockets.legacy is deprecated",
            DeprecationWarning,
            filename="legacy.py",
            lineno=1,
            module="websockets.legacy.client",
        )
        return ModuleType(module_name)

    monkeypatch.setattr(
        "relay_teams.gateway.feishu.lark_ws_compat.importlib.import_module",
        _fake_import,
    )
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("default")
            module = import_lark_module("lark_oapi.event.dispatcher_handler")
        assert module.__name__ == "lark_oapi.event.dispatcher_handler"
        assert caught == []
    finally:
        pass


def test_parse_ws_conn_exception_reads_invalid_status_response_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = Headers()
    headers["handshake-status"] = "403"
    headers["handshake-msg"] = "forbidden"

    fake_const = SimpleNamespace(
        AUTH_FAILED=401,
        EXCEED_CONN_LIMIT=99991672,
        FORBIDDEN=403,
        HEADER_HANDSHAKE_AUTH_ERRCODE="handshake-auth-errcode",
        HEADER_HANDSHAKE_MSG="handshake-msg",
        HEADER_HANDSHAKE_STATUS="handshake-status",
    )

    class _ClientException(Exception):
        pass

    class _ServerException(Exception):
        pass

    fake_exception = SimpleNamespace(
        ClientException=_ClientException,
        ServerException=_ServerException,
    )

    original_import = __import__

    def _fake_import(name: str, globals=None, locals=None, fromlist=(), level: int = 0):
        if name == "lark_oapi.ws.const":
            return fake_const
        if name == "lark_oapi.ws.exception":
            return fake_exception
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    with pytest.raises(_ClientException, match="forbidden"):
        _parse_ws_conn_exception(InvalidStatus(Response(403, "Forbidden", headers)))


def test_feishu_ws_controller_receive_loop_ignores_normal_close_after_stop() -> None:
    controller = _FeishuWsController(
        runtime_config=_build_runtime(
            trigger_id="trg_a",
            name="bot_a",
            app_id="cli_demo",
            app_name="bot-a",
            app_secret="secret-demo",
        ),
        event_handler=_FakeHandler(),
    )

    class _ClosingWsConnection:
        async def close(self) -> None:
            return None

        async def recv(self) -> bytes | str:
            controller._stop_requested = True
            raise ConnectionClosedOK(Close(1000, "bye"), Close(1000, ""), False)

    ws_client = _FakeWsClient()
    ws_client._conn = cast(_FakeWsConnection, _ClosingWsConnection())

    asyncio.run(controller._receive_loop(cast(WsClientLike, ws_client)))
