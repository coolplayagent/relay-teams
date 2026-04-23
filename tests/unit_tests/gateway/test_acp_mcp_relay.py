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
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
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


@pytest.mark.asyncio
async def test_gateway_aware_mcp_registry_exposes_session_scoped_stdio_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = AcpMcpRelay()
    built_specs: list[McpServerSpec] = []

    def fake_build_mcp_server(spec: McpServerSpec) -> _FakeToolset:
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
