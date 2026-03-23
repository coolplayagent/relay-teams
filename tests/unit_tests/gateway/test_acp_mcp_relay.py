# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

import pytest
from pydantic import JsonValue

import agent_teams.gateway.acp_mcp_relay as acp_mcp_relay_module
from agent_teams.gateway.acp_mcp_relay import (
    AcpMcpServer,
    AcpMcpConnectionTransport,
    AcpMcpRelay,
    GatewayAwareMcpRegistry,
)
from agent_teams.gateway.gateway_models import GatewayMcpServerSpec
from agent_teams.mcp.mcp_models import McpServerSpec
from agent_teams.mcp.mcp_registry import McpRegistry


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
