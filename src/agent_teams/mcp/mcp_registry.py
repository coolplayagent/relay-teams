# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai.mcp import (
    MCPServer,
    MCPServerSSE,
    MCPServerStdio,
    MCPServerStreamableHTTP,
)

from agent_teams.logger import get_logger
from agent_teams.mcp.mcp_models import McpServerSpec, McpToolInfo, McpToolSchema
from agent_teams.trace import trace_span

LOGGER = get_logger(__name__)
_DEFAULT_STDIO_MCP_TIMEOUT_SECONDS = 15.0


class _ListedMcpTool(Protocol):
    name: object
    description: object
    inputSchema: object


class McpRegistry:
    def __init__(self, specs: tuple[McpServerSpec, ...] = ()) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._toolsets: dict[str, MCPServer] = {}

    def get_toolsets(self, names: tuple[str, ...]) -> tuple[MCPServer, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="get_toolsets",
            attributes={"server_names": list(names)},
        ):
            self.validate_known(names)
            toolsets: list[MCPServer] = []
            for name in names:
                toolsets.append(self._get_or_create_toolset(name))
            return tuple(toolsets)

    def validate_known(self, names: tuple[str, ...]) -> None:
        missing = [name for name in names if name not in self._specs]
        if missing:
            raise ValueError(f"Unknown MCP servers: {missing}")

    def resolve_server_names(self, names: tuple[str, ...]) -> tuple[str, ...]:
        self.validate_known(names)
        return names

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))

    def list_specs(self) -> tuple[McpServerSpec, ...]:
        return tuple(self._specs[name] for name in self.list_names())

    def get_spec(self, name: str) -> McpServerSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"Unknown MCP server: {name}")
        return spec

    async def list_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="list_tools",
            attributes={"server_name": name},
        ):
            mcp_tools = await self._list_tool_objects(name)
            return tuple(
                McpToolInfo(
                    name=str(tool.name),
                    description=tool.description
                    if isinstance(tool.description, str)
                    else "",
                )
                for tool in mcp_tools
            )

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="list_tool_schemas",
            attributes={"server_name": name},
        ):
            mcp_tools = await self._list_tool_objects(name)
            return tuple(
                McpToolSchema(
                    name=str(tool.name),
                    description=tool.description
                    if isinstance(tool.description, str)
                    else "",
                    input_schema=(
                        dict(tool.inputSchema)
                        if isinstance(tool.inputSchema, dict)
                        else {}
                    ),
                )
                for tool in mcp_tools
            )

    async def _list_tool_objects(self, name: str) -> tuple[_ListedMcpTool, ...]:
        toolset = self._get_or_create_toolset(name)
        async with toolset:
            mcp_tools = await toolset.list_tools()
        return cast("tuple[_ListedMcpTool, ...]", tuple(mcp_tools))

    def _get_or_create_toolset(self, name: str) -> MCPServer:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="get_or_create_toolset",
            attributes={"server_name": name},
        ):
            existing = self._toolsets.get(name)
            if existing is not None:
                return existing

            spec = self.get_spec(name)
            toolset = build_mcp_server(spec)
            self._toolsets[name] = toolset
            return toolset


def build_mcp_server(spec: McpServerSpec) -> MCPServer:
    server_config = spec.server_config
    transport = _detect_transport(server_config)
    if transport == "stdio":
        command = _required_string(server_config, "command")
        return MCPServerStdio(
            command=command,
            args=_string_list(server_config.get("args")),
            env=_string_dict(server_config.get("env")),
            cwd=_optional_string(server_config.get("cwd")),
            timeout=(
                _optional_positive_float(server_config.get("timeout"))
                or _DEFAULT_STDIO_MCP_TIMEOUT_SECONDS
            ),
            read_timeout=(
                _optional_positive_float(server_config.get("read_timeout")) or 300.0
            ),
            id=spec.name,
        )
    if transport == "sse":
        url = _required_string(server_config, "url")
        return MCPServerSSE(
            url=url,
            headers=_string_dict(server_config.get("headers")),
            id=spec.name,
        )
    if transport == "http":
        url = _required_string(server_config, "url")
        return MCPServerStreamableHTTP(
            url=url,
            headers=_string_dict(server_config.get("headers")),
            id=spec.name,
        )
    raise ValueError(f"Unsupported MCP transport: {transport}")


def _detect_transport(server_config: Mapping[str, JsonValue]) -> str:
    raw_transport = server_config.get("transport")
    if isinstance(raw_transport, str) and raw_transport.strip():
        return raw_transport.strip()
    raw_type = server_config.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        return raw_type.strip()
    if isinstance(server_config.get("command"), str):
        return "stdio"
    raw_url = server_config.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "sse" if "/sse" in raw_url else "http"
    raise ValueError("Unable to detect MCP transport")


def _required_string(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError(f"{key} must be a non-empty string")


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _string_list(value: JsonValue) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _string_dict(value: JsonValue) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): str(item) for key, item in value.items() if isinstance(key, str)}
