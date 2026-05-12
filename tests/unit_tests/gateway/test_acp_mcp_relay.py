# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast
from datetime import datetime, timezone

import pytest
from pydantic import JsonValue

import relay_teams.gateway.acp_mcp_relay as acp_mcp_relay_module
from relay_teams.gateway.acp_mcp_relay import (
    AcpMcpServer,
    AcpMcpConnectionTransport,
    AcpMcpRelay,
    GatewayAwareMcpRegistry,
)
from relay_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from relay_teams.metrics import (
    DEFAULT_DEFINITIONS,
    MetricEvent,
    MetricRecorder,
    MetricRegistry,
)
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec, McpToolInfo
from relay_teams.mcp.mcp_registry import McpRegistry


class _MetricEventSink:
    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    def record(self, event: MetricEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_acp_mcp_connection_transport_relays_initialize_list_and_call() -> None:
    outbound_requests: list[tuple[str, dict[str, JsonValue]]] = []
    outbound_notifications: list[dict[str, JsonValue]] = []

    async def send_request(
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        outbound_requests.append((method, params))
        assert method == "mcp/message"
        inner_method = params["method"]
        assert isinstance(inner_method, str)
        if inner_method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "zed-mcp",
                        "version": "1.0.0",
                    },
                },
            }
        if inner_method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text back",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                },
                                "required": ["text"],
                            },
                        }
                    ]
                },
            }
        if inner_method == "tools/call":
            inner_params = params.get("params")
            assert isinstance(inner_params, dict)
            arguments = inner_params.get("arguments")
            assert isinstance(arguments, dict)
            return {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"echo: {arguments['text']}",
                        }
                    ],
                    "structuredContent": {
                        "text": arguments["text"],
                    },
                    "isError": False,
                },
            }
        raise AssertionError(f"Unexpected MCP method: {inner_method}")

    async def send_notification(message: dict[str, JsonValue]) -> None:
        outbound_notifications.append(message)

    transport = AcpMcpConnectionTransport(
        session_id="gws_123",
        connection_id="conn_123",
        send_request=send_request,
        send_notification=send_notification,
    )
    toolset = AcpMcpServer(transport=transport, id="zed-tools")

    async with toolset:
        tools = await toolset.list_tools()
        call_result = await toolset.direct_call_tool("echo", {"text": "hello"})

    assert [tool.name for tool in tools] == ["echo"]
    assert cast(dict[str, JsonValue], call_result) == {"text": "hello"}
    assert [params["method"] for _, params in outbound_requests] == [
        "initialize",
        "tools/list",
        "tools/call",
    ]
    assert outbound_notifications == [
        {
            "jsonrpc": "2.0",
            "method": "mcp/message",
            "params": {
                "sessionId": "gws_123",
                "connectionId": "conn_123",
                "method": "notifications/initialized",
            },
        }
    ]


@pytest.mark.asyncio
async def test_acp_mcp_connection_transport_records_bridge_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = _MetricEventSink()
    recorder = MetricRecorder(
        registry=MetricRegistry(DEFAULT_DEFINITIONS),
        sinks=(sink,),
    )
    recorded_events: list[dict[str, object]] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, JsonValue] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = exc_info
        recorded_events.append(
            {
                "event": event,
                "message": message,
                "payload": payload,
                "duration_ms": duration_ms,
            }
        )

    monkeypatch.setattr(acp_mcp_relay_module, "log_event", fake_log_event)

    async def send_request(
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        assert method == "mcp/message"
        inner_method = params["method"]
        assert isinstance(inner_method, str)
        if inner_method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "zed-mcp", "version": "1.0.0"},
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [],
            },
        }

    async def send_notification(_message: dict[str, JsonValue]) -> None:
        return None

    def lookup_gateway_session(_gateway_session_id: str) -> GatewaySessionRecord:
        return GatewaySessionRecord(
            gateway_session_id="gws_123",
            channel_type=GatewayChannelType.ACP_STDIO,
            external_session_id="gws_123",
            internal_session_id="session-1",
            active_run_id="run-1",
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
        )

    transport = AcpMcpConnectionTransport(
        session_id="gws_123",
        connection_id="conn_123",
        send_request=send_request,
        send_notification=send_notification,
        metric_recorder=recorder,
        gateway_session_lookup=lookup_gateway_session,
    )
    toolset = AcpMcpServer(transport=transport, id="zed-tools")

    async with toolset:
        _ = await toolset.list_tools()

    operation_events = [
        event
        for event in sink.events
        if event.definition_name == "relay_teams.gateway.operations"
    ]
    assert len(operation_events) == 2
    assert operation_events[0].tags.gateway_operation == "mcp_bridge_request"
    assert operation_events[0].tags.session_id == "session-1"
    assert operation_events[0].tags.run_id == "run-1"
    assert operation_events[0].tags.gateway_transport == "acp"
    assert recorded_events[0]["event"] == "gateway.acp.mcp_bridge.completed"


@pytest.mark.asyncio
async def test_acp_mcp_connection_transport_logs_bridge_subexception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded_events: list[dict[str, object]] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, JsonValue] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = duration_ms
        recorded_events.append(
            {
                "event": event,
                "message": message,
                "payload": payload,
                "exc_info": exc_info,
            }
        )

    monkeypatch.setattr(acp_mcp_relay_module, "log_event", fake_log_event)

    async def send_request(
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _ = (method, params)
        raise RuntimeError("boom from send_request")

    async def send_notification(_message: dict[str, JsonValue]) -> None:
        return None

    transport = AcpMcpConnectionTransport(
        session_id="gws_123",
        connection_id="conn_123",
        send_request=send_request,
        send_notification=send_notification,
    )
    toolset = AcpMcpServer(transport=transport, id="zed-tools")

    with pytest.raises(ExceptionGroup) as exc_info:
        async with toolset:
            await toolset.list_tools()

    assert len(exc_info.value.exceptions) == 1
    assert str(exc_info.value.exceptions[0]) == "boom from send_request"
    assert len(recorded_events) == 2
    assert recorded_events[0]["event"] == "gateway.acp.mcp_bridge.failed"
    assert recorded_events[0]["payload"] == {
        "gateway_session_id": "gws_123",
        "connection_id": "conn_123",
        "method": "initialize",
        "status": "failed",
        "gateway_operation": "mcp_bridge_request",
        "gateway_phase": "request",
        "gateway_transport": "acp",
    }
    assert recorded_events[0]["exc_info"] == exc_info.value.exceptions[0]
    assert recorded_events[1] == {
        "event": "gateway.acp_mcp_relay.bridge.failed",
        "message": "ACP MCP relay bridge task failed",
        "payload": {
            "session_id": "gws_123",
            "connection_id": "conn_123",
            "pending_request_count": 0,
            "message_kind": "request",
            "method": "initialize",
            "message_id": 0,
        },
        "exc_info": exc_info.value.exceptions[0],
    }


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_exposes_session_scoped_acp_servers() -> None:
    relay = AcpMcpRelay()

    async def send_request(
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        assert method == "mcp/message"
        inner_method = params["method"]
        assert isinstance(inner_method, str)
        if inner_method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "zed-mcp",
                        "version": "1.0.0",
                    },
                },
            }
        if inner_method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text back",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                },
                            },
                        }
                    ]
                },
            }
        raise AssertionError(f"Unexpected MCP method: {inner_method}")

    async def send_notification(_message: dict[str, JsonValue]) -> None:
        return None

    relay.set_outbound(
        send_request=send_request,
        send_notification=send_notification,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "id": "zed-tools"},
            ),
        ),
    )
    await relay.open_connection(
        session_id="gws_123",
        connection_id="conn_123",
        server_spec=GatewayMcpServerSpec(
            server_id="zed-tools",
            name="zed-tools",
            transport="acp",
            config={"transport": "acp", "id": "zed-tools"},
        ),
    )

    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        assert registry.resolve_server_names(()) == ("zed-tools",)
        tools = await registry.list_tools("zed-tools")
        schemas = await registry.list_tool_schemas("zed-tools")

    await relay.close_connection(connection_id="conn_123")

    assert [tool.name for tool in tools] == ["zed-tools_echo"]
    assert schemas[0].name == "zed-tools_echo"
    assert cast(dict[str, JsonValue], schemas[0].input_schema)["type"] == "object"


def test_gateway_aware_mcp_registry_supports_wildcard_resolution() -> None:
    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "id": "zed-tools"},
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(
            (
                McpServerSpec(
                    name="filesystem",
                    config={"mcpServers": {"filesystem": {"command": "npx"}}},
                    server_config={"command": "npx"},
                    source=McpConfigScope.APP,
                ),
            )
        ),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        assert registry.resolve_server_names(("*",)) == ("filesystem", "zed-tools")
        assert registry.resolve_server_names(
            ("*",),
            expand_wildcards=False,
            strict=False,
        ) == ("*", "zed-tools")


def test_gateway_aware_mcp_registry_filters_unknowns_after_wildcard() -> None:
    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "id": "zed-tools"},
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(
            (
                McpServerSpec(
                    name="filesystem",
                    config={"mcpServers": {"filesystem": {"command": "npx"}}},
                    server_config={"command": "npx"},
                    source=McpConfigScope.APP,
                ),
            )
        ),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        resolved = registry.resolve_server_names(
            ("*", "missing", "filesystem"),
            strict=False,
        )

    assert resolved == ("filesystem", "zed-tools")


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_prepares_w3_env_on_base_registry() -> None:
    class _TrackingMcpRegistry(McpRegistry):
        def __init__(self) -> None:
            super().__init__(
                (
                    McpServerSpec(
                        name="filesystem",
                        config={"mcpServers": {"filesystem": {"command": "npx"}}},
                        server_config={
                            "command": "npx",
                            "env": {"X_AUTH_TOKEN": "placeholder"},
                        },
                        source=McpConfigScope.APP,
                    ),
                )
            )
            self.prepare_calls: list[tuple[tuple[str, ...], bool, str | None]] = []

        async def prepare_w3_auth_env(
            self,
            names: tuple[str, ...],
            *,
            strict: bool = True,
            consumer: str | None = None,
        ) -> None:
            self.prepare_calls.append((names, strict, consumer))

    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "id": "zed-tools"},
            ),
        ),
    )
    base_registry = _TrackingMcpRegistry()
    registry = GatewayAwareMcpRegistry(
        base_registry=base_registry,
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        await registry.prepare_w3_auth_env(
            ("*",),
            strict=False,
            consumer="test.consumer",
        )

    assert base_registry.prepare_calls == [(("filesystem",), False, "test.consumer")]


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_prepares_w3_env_for_session_stdio_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    built_tokens: list[str | None] = []

    async def fake_resolve_w3_x_auth_token() -> str:
        return "runtime-token"

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        built_tokens.append(w3_x_auth_token)
        return _FakeToolset((), tool_prefix=spec.name)

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "resolve_w3_x_auth_token",
        fake_resolve_w3_x_auth_token,
    )
    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="w3-session",
                name="w3-session",
                transport="stdio",
                config={
                    "command": "npx",
                    "env": {"xAuthToken": "placeholder"},
                },
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    relay.prepare_current_session_w3_auth_token(("w3-session",), token="ignored")
    assert relay.current_session_w3_auth_tokens() == ()
    assert acp_mcp_relay_module._gateway_server_declares_w3_auth_env(None) is False

    with relay.session_scope("gws_123"):
        await registry.prepare_w3_auth_env(("w3-session",), consumer="test.consumer")
        assert registry.runtime_w3_x_auth_token() == "runtime-token"
        toolsets = registry.get_toolsets(("w3-session",))

    assert len(toolsets) == 1
    assert built_tokens == ["runtime-token"]
    assert registry.runtime_w3_x_auth_token() is None

    relay.bind_session_servers("gws_123", ())
    with relay.session_scope("gws_123"):
        assert registry.runtime_w3_x_auth_token() is None


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_prepares_w3_env_for_session_local_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    built_tokens: list[str | None] = []

    async def fake_resolve_w3_x_auth_token() -> str:
        return "runtime-token"

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        built_tokens.append(w3_x_auth_token)
        return _FakeToolset((), tool_prefix=spec.name)

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "resolve_w3_x_auth_token",
        fake_resolve_w3_x_auth_token,
    )
    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    local_spec = GatewayMcpServerSpec(
        server_id="local-session",
        name="local-session",
        transport="local",
        config={
            "type": "local",
            "command": "npx",
            "env": {"X_AUTH_TOKEN": "placeholder"},
        },
    )
    relay.bind_session_servers("gws_123", (local_spec,))
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        await registry.prepare_w3_auth_env(
            ("local-session",),
            consumer="test.consumer",
        )
        assert registry.get_toolsets(("local-session",))

    assert built_tokens == ["runtime-token"]
    assert acp_mcp_relay_module._gateway_server_declares_w3_auth_env(local_spec) is True


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_prepares_shadowed_session_w3_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _TrackingMcpRegistry(McpRegistry):
        def __init__(self) -> None:
            super().__init__(
                (
                    McpServerSpec(
                        name="shared",
                        config={"mcpServers": {"shared": {"command": "npx"}}},
                        server_config={
                            "command": "npx",
                            "env": {"AUTH_TOKEN": "base-placeholder"},
                        },
                        source=McpConfigScope.APP,
                    ),
                )
            )
            self.prepare_calls: list[tuple[tuple[str, ...], bool, str | None]] = []

        async def prepare_w3_auth_env(
            self,
            names: tuple[str, ...],
            *,
            strict: bool = True,
            consumer: str | None = None,
        ) -> None:
            self.prepare_calls.append((names, strict, consumer))

    relay = AcpMcpRelay()
    built_tokens: list[str | None] = []

    async def fake_resolve_w3_x_auth_token() -> str:
        return "runtime-token"

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        built_tokens.append(w3_x_auth_token)
        return _FakeToolset((), tool_prefix=spec.name)

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "resolve_w3_x_auth_token",
        fake_resolve_w3_x_auth_token,
    )
    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="shared",
                name="shared",
                transport="stdio",
                config={
                    "command": "npx",
                    "env": {"X_AUTH_TOKEN": "session-placeholder"},
                },
            ),
        ),
    )
    base_registry = _TrackingMcpRegistry()
    registry = GatewayAwareMcpRegistry(
        base_registry=base_registry,
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        spec = registry.get_w3_auth_env_spec("shared")
        raw_env = spec.server_config["env"]
        assert isinstance(raw_env, dict)
        assert acp_mcp_relay_module.env_declares_w3_x_auth_token(
            raw_env,
        )
        await registry.prepare_w3_auth_env(("shared",), consumer="test.consumer")
        assert registry.get_toolsets(("shared",))

    assert base_registry.prepare_calls == []
    assert built_tokens == ["runtime-token"]


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_preserves_and_reenables_session_w3_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    resolved_tokens: list[str | None] = ["runtime-token", None]
    built_tokens: list[str | None] = []

    async def fake_resolve_w3_x_auth_token() -> str | None:
        return resolved_tokens.pop(0)

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        built_tokens.append(w3_x_auth_token)
        if w3_x_auth_token is None:
            raise RuntimeError("startup failed")
        return _FakeToolset((), tool_prefix=spec.name)

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "resolve_w3_x_auth_token",
        fake_resolve_w3_x_auth_token,
    )
    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="w3-session",
                name="w3-session",
                transport="stdio",
                config={
                    "command": "npx",
                    "env": {"X_AUTH_TOKEN": "placeholder"},
                },
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        assert registry.get_toolsets(("w3-session",)) == ()
        await registry.prepare_w3_auth_env(("w3-session",), consumer="test.consumer")
        toolsets = registry.get_toolsets(("w3-session",))
        assert registry.runtime_w3_x_auth_token() == "runtime-token"
        await registry.prepare_w3_auth_env(("w3-session",), consumer="test.consumer")
        preserved_toolsets = registry.get_toolsets(("w3-session",))
        assert registry.runtime_w3_x_auth_token() == "runtime-token"

    assert len(toolsets) == 1
    assert preserved_toolsets == toolsets
    assert built_tokens == [None, "runtime-token"]


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_discovers_session_stdio_with_w3_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    built_tokens: list[str | None] = []

    async def fake_resolve_w3_x_auth_token() -> str:
        return "runtime-token"

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        built_tokens.append(w3_x_auth_token)
        return _FakeToolset(
            (
                _FakeListedTool(
                    name="echo",
                    description="Echo",
                    input_schema={"type": "object"},
                ),
            ),
            tool_prefix=spec.name,
        )

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "resolve_w3_x_auth_token",
        fake_resolve_w3_x_auth_token,
    )
    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="w3-session",
                name="w3-session",
                transport="stdio",
                config={
                    "command": "npx",
                    "env": {"X_AUTH_TOKEN": "placeholder"},
                },
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        await registry.prepare_w3_auth_env(("w3-session",), consumer="test.consumer")
        tools = await registry.list_tools_for_discovery("w3-session")

    assert built_tokens == ["runtime-token"]
    assert tools == (
        McpToolInfo(
            name="w3-session_echo",
            description="Echo",
        ),
    )


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_discovery_delegates_without_session() -> None:
    class _TrackingMcpRegistry(McpRegistry):
        def __init__(self) -> None:
            super().__init__(())
            self.calls: list[str] = []

        async def list_tools_for_discovery(
            self,
            name: str,
        ) -> tuple[McpToolInfo, ...]:
            self.calls.append(name)
            return (McpToolInfo(name="base_echo", description="Echo"),)

    base_registry = _TrackingMcpRegistry()
    registry = GatewayAwareMcpRegistry(
        base_registry=base_registry,
        relay=AcpMcpRelay(),
    )

    tools = await registry.list_tools_for_discovery("base")

    assert tools == (McpToolInfo(name="base_echo", description="Echo"),)
    assert base_registry.calls == ["base"]


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_discovery_returns_empty_for_inactive_acp() -> (
    None
):
    relay = AcpMcpRelay()
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "id": "zed-tools"},
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        tools = await registry.list_tools_for_discovery("zed-tools")

    assert tools == ()
    assert (
        acp_mcp_relay_module._gateway_server_declares_w3_auth_env(
            GatewayMcpServerSpec(
                server_id="zed-tools",
                name="zed-tools",
                transport="acp",
                config={"transport": "acp", "env": {"X_AUTH_TOKEN": "placeholder"}},
            )
        )
        is False
    )


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_discovery_disables_failed_stdio_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    calls = 0

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        _ = spec, w3_x_auth_token
        nonlocal calls
        calls += 1
        raise RuntimeError("startup failed")

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="w3-session",
                name="w3-session",
                transport="stdio",
                config={"command": "npx"},
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        with pytest.raises(RuntimeError, match="startup failed"):
            await registry.list_tools_for_discovery("w3-session")
        tools = await registry.list_tools_for_discovery("w3-session")

    assert tools == ()
    assert calls == 1


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_discovery_disables_failed_tool_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FailingListToolset:
        _ = spec, w3_x_auth_token
        return _FailingListToolset(
            (),
            error=RuntimeError("listing failed"),
            tool_prefix=spec.name,
        )

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="w3-session",
                name="w3-session",
                transport="stdio",
                config={"command": "npx"},
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        with pytest.raises(RuntimeError, match="listing failed"):
            await registry.list_tools_for_discovery("w3-session")
        tools = await registry.list_tools_for_discovery("w3-session")

    assert tools == ()


def test_acp_mcp_relay_prunes_session_w3_tokens_when_servers_rebind() -> None:
    relay = AcpMcpRelay()

    with relay.session_scope("other-session"):
        relay.prepare_current_session_w3_auth_token(("other",), token="other-token")
    with relay.session_scope("gws_123"):
        relay.prepare_current_session_w3_auth_token(("kept",), token="kept-token")
        relay.prepare_current_session_w3_auth_token(("removed",), token="old-token")

    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="kept",
                name="kept",
                transport="stdio",
                config={"command": "npx"},
            ),
        ),
    )

    with relay.session_scope("gws_123"):
        assert relay.current_session_w3_auth_tokens() == ("kept-token",)
        relay.prepare_current_session_w3_auth_token(("kept",), token=None)
        assert relay.current_session_w3_auth_tokens() == ()
    with relay.session_scope("other-session"):
        assert relay.current_session_w3_auth_tokens() == ("other-token",)


def test_gateway_aware_mcp_registry_validates_exact_wildcard_only() -> None:
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=AcpMcpRelay(),
    )

    registry.validate_known(("*",))
    assert registry.resolve_server_names(
        ("*",),
        expand_wildcards=False,
        strict=True,
    ) == ("*",)
    with pytest.raises(ValueError, match="Unknown MCP servers: \\['mcp-\\*'\\]"):
        registry.validate_known(("mcp-*",))


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_ignores_unconnected_session_scoped_acp_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    recorded_events: list[dict[str, object]] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, JsonValue] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = duration_ms
        recorded_events.append(
            {
                "event": event,
                "message": message,
                "payload": payload,
                "exc_info": exc_info,
            }
        )

    monkeypatch.setattr(acp_mcp_relay_module, "log_event", fake_log_event)

    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="cat-cafe",
                name="cat-cafe",
                transport="acp",
                config={"transport": "acp", "id": "cat-cafe"},
            ),
        ),
    )

    with relay.session_scope("gws_123"):
        tools = await registry.list_tools("cat-cafe")
        toolsets = registry.get_toolsets(("cat-cafe",))

    assert tools == ()
    assert toolsets == ()
    assert recorded_events == [
        {
            "event": "mcp.registry.session_server_ignored",
            "message": "Ignoring unavailable session-scoped MCP server",
            "payload": {
                "server_name": "cat-cafe",
                "reason": "acp_connection_inactive",
                "consumer": "gateway.acp_mcp_relay.list_tools",
                "session_id": "gws_123",
                "transport": "acp",
            },
            "exc_info": None,
        },
        {
            "event": "mcp.registry.session_server_ignored",
            "message": "Ignoring unavailable session-scoped MCP server",
            "payload": {
                "server_name": "cat-cafe",
                "reason": "acp_connection_inactive",
                "consumer": "gateway.acp_mcp_relay.get_toolsets",
                "session_id": "gws_123",
                "transport": "acp",
            },
            "exc_info": None,
        },
    ]


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_disables_failed_session_scoped_acp_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    recorded_events: list[dict[str, object]] = []

    def fake_log_event(
        _logger: object,
        _level: int,
        *,
        event: str,
        message: str,
        payload: dict[str, JsonValue] | None = None,
        duration_ms: int | None = None,
        exc_info: object = None,
    ) -> None:
        _ = duration_ms
        recorded_events.append(
            {
                "event": event,
                "message": message,
                "payload": payload,
                "exc_info": exc_info,
            }
        )

    monkeypatch.setattr(acp_mcp_relay_module, "log_event", fake_log_event)

    async def send_request(
        method: str,
        params: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _ = (method, params)
        raise RuntimeError("cat-cafe initialize failed")

    async def send_notification(_message: dict[str, JsonValue]) -> None:
        return None

    relay.set_outbound(
        send_request=send_request,
        send_notification=send_notification,
    )
    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="cat-cafe",
                name="cat-cafe",
                transport="acp",
                config={"transport": "acp", "id": "cat-cafe"},
            ),
        ),
    )
    await relay.open_connection(
        session_id="gws_123",
        connection_id="conn_123",
        server_spec=GatewayMcpServerSpec(
            server_id="cat-cafe",
            name="cat-cafe",
            transport="acp",
            config={"transport": "acp", "id": "cat-cafe"},
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        tools = await registry.list_tools("cat-cafe")
        toolsets = registry.get_toolsets(("cat-cafe",))

    await relay.close_connection(connection_id="conn_123")

    assert tools == ()
    assert toolsets == ()
    assert len(recorded_events) == 3
    assert recorded_events[0]["event"] == "gateway.acp.mcp_bridge.failed"
    assert recorded_events[1]["event"] == "gateway.acp_mcp_relay.bridge.failed"
    assert recorded_events[2] == {
        "event": "mcp.registry.session_server_ignored",
        "message": "Ignoring unavailable session-scoped MCP server",
        "payload": {
            "server_name": "cat-cafe",
            "reason": "tool_listing_failed",
            "consumer": "gateway.acp_mcp_relay.list_tools",
            "session_id": "gws_123",
            "transport": "acp",
        },
        "exc_info": recorded_events[1]["exc_info"],
    }
    assert str(recorded_events[1]["exc_info"]) == "cat-cafe initialize failed"


class _FakeListedTool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, JsonValue],
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class _FakeToolset:
    def __init__(
        self,
        tools: tuple[_FakeListedTool, ...],
        *,
        tool_prefix: str | None = None,
    ) -> None:
        self._tools = tools
        self.tool_prefix = tool_prefix

    async def __aenter__(self) -> _FakeToolset:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        _ = (exc_type, exc, tb)

    async def list_tools(self) -> tuple[_FakeListedTool, ...]:
        return self._tools


class _FailingListToolset(_FakeToolset):
    def __init__(
        self,
        tools: tuple[_FakeListedTool, ...],
        *,
        error: RuntimeError,
        tool_prefix: str | None = None,
    ) -> None:
        super().__init__(tools, tool_prefix=tool_prefix)
        self._error = error

    async def list_tools(self) -> tuple[_FakeListedTool, ...]:
        raise self._error


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_exposes_session_scoped_stdio_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    built_specs: list[McpServerSpec] = []

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        w3_x_auth_token: str | None = None,
        **_kwargs: object,
    ) -> _FakeToolset:
        _ = w3_x_auth_token
        built_specs.append(spec)
        return _FakeToolset(
            (
                _FakeListedTool(
                    name="resolve-library-id",
                    description="Resolve a library name",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "libraryName": {"type": "string"},
                        },
                    },
                ),
            ),
            tool_prefix=spec.name,
        )

    monkeypatch.setattr(
        acp_mcp_relay_module,
        "build_mcp_server",
        fake_build_mcp_server,
    )

    relay.bind_session_servers(
        "gws_123",
        (
            GatewayMcpServerSpec(
                server_id="mcp-server-context7",
                name="mcp-server-context7",
                transport="stdio",
                config={
                    "command": "npx",
                    "args": ["-y", "@upstash/context7-mcp"],
                },
            ),
        ),
    )
    registry = GatewayAwareMcpRegistry(
        base_registry=McpRegistry(()),
        relay=relay,
    )

    with relay.session_scope("gws_123"):
        assert registry.resolve_server_names(()) == ("mcp-server-context7",)
        toolsets = registry.get_toolsets(("mcp-server-context7",))
        tools = await registry.list_tools("mcp-server-context7")

    assert len(toolsets) == 1
    assert toolsets[0].tool_prefix == "mcp-server-context7"
    assert len(built_specs) == 1
    assert built_specs[0].name == "mcp-server-context7"
    assert built_specs[0].server_config["command"] == "npx"
    assert [tool.name for tool in tools] == ["mcp-server-context7_resolve-library-id"]
