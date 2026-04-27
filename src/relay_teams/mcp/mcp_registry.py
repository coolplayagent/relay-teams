# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Protocol, cast

from pydantic import JsonValue
from pydantic_ai.mcp import (
    MCPServer,
    MCPServerSSE,
    MCPServerStdio,
    MCPServerStreamableHTTP,
)

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import McpServerSpec, McpToolInfo, McpToolSchema
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)
_DEFAULT_STDIO_MCP_TIMEOUT_SECONDS = 15.0
_CAPABILITY_WILDCARD = "*"


class _ListedMcpTool(Protocol):
    name: object
    description: object
    inputSchema: object


def get_mcp_tool_prefix(server_name: str) -> str:
    return server_name.strip()


def get_effective_mcp_tool_name(server_name: str, tool_name: str) -> str:
    prefix = get_mcp_tool_prefix(server_name)
    if not prefix:
        return tool_name
    return f"{prefix}_{tool_name}"


class McpRegistry:
    def __init__(self, specs: tuple[McpServerSpec, ...] = ()) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self._toolsets: dict[str, MCPServer] = {}
        self._runtime_failed_names: set[str] = set()

    def get_toolsets(self, names: tuple[str, ...]) -> tuple[MCPServer, ...]:
        with trace_span(
            LOGGER,
            component="mcp.registry",
            operation="get_toolsets",
            attributes={"server_names": list(names)},
        ):
            resolved_names = self.resolve_server_names(names)
            toolsets: list[MCPServer] = []
            for name in resolved_names:
                if self.is_server_runtime_failed(name):
                    continue
                toolsets.append(self._get_or_create_toolset(name))
            return tuple(toolsets)

    def validate_known(self, names: tuple[str, ...]) -> None:
        _ = self.resolve_server_names(names)

    def resolve_server_names(
        self,
        names: tuple[str, ...],
        *,
        strict: bool = True,
        consumer: str | None = None,
        expand_wildcards: bool = True,
    ) -> tuple[str, ...]:
        resolved_names, missing_names = self._resolve_names(
            names,
            expand_wildcards=expand_wildcards,
        )
        if missing_names and strict:
            raise ValueError(f"Unknown MCP servers: {list(missing_names)}")
        if missing_names:
            payload: dict[str, JsonValue] = {
                "requested_server_names": list(names),
                "resolved_server_names": list(resolved_names),
                "ignored_server_names": list(missing_names),
            }
            if consumer is not None:
                payload["consumer"] = consumer
            log_event(
                LOGGER,
                logging.WARNING,
                event="mcp.registry.unknown_ignored",
                message="Ignoring unknown MCP servers from existing configuration",
                payload=payload,
            )
        return resolved_names

    def _resolve_names(
        self,
        names: tuple[str, ...],
        *,
        expand_wildcards: bool,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        resolved_names: list[str] = []
        wildcard_names: set[str] = set()
        missing_names: list[str] = []
        for raw_name in names:
            name = raw_name.strip()
            if name == _CAPABILITY_WILDCARD:
                if not expand_wildcards:
                    if _CAPABILITY_WILDCARD not in resolved_names:
                        resolved_names.append(_CAPABILITY_WILDCARD)
                    continue
                for server_name in self.list_enabled_names():
                    if server_name not in resolved_names:
                        resolved_names.append(server_name)
                    wildcard_names.add(server_name)
                continue
            if name in self._specs and self._specs[name].enabled:
                if name not in wildcard_names:
                    resolved_names.append(name)
                continue
            missing_names.append(name)
        return tuple(resolved_names), tuple(missing_names)

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs.keys()))

    def list_enabled_names(self) -> tuple[str, ...]:
        return tuple(name for name in self.list_names() if self._specs[name].enabled)

    def is_server_runtime_failed(self, name: str) -> bool:
        return name.strip() in self._runtime_failed_names

    def mark_server_runtime_failed(self, name: str) -> None:
        normalized_name = name.strip()
        if normalized_name:
            self._runtime_failed_names.add(normalized_name)

    def mark_server_runtime_available(self, name: str) -> None:
        self._runtime_failed_names.discard(name.strip())

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
                    name=get_effective_mcp_tool_name(name, str(tool.name)),
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
                    name=get_effective_mcp_tool_name(name, str(tool.name)),
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
        try:
            toolset = self._get_or_create_toolset(name)
            async with toolset:
                mcp_tools = await toolset.list_tools()
        except Exception:
            self.mark_server_runtime_failed(name)
            raise
        self.mark_server_runtime_available(name)
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
            if not spec.enabled:
                raise ValueError(f"MCP server is disabled: {name}")
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
            env=_build_stdio_env(server_config),
            cwd=_optional_string(server_config.get("cwd")),
            tool_prefix=get_mcp_tool_prefix(spec.name),
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
            tool_prefix=get_mcp_tool_prefix(spec.name),
        )
    if transport in ("http", "streamable-http"):
        url = _required_string(server_config, "url")
        return MCPServerStreamableHTTP(
            url=url,
            headers=_string_dict(server_config.get("headers")),
            id=spec.name,
            tool_prefix=get_mcp_tool_prefix(spec.name),
        )
    raise ValueError(f"Unsupported MCP transport: {transport}")


def _detect_transport(server_config: Mapping[str, JsonValue]) -> str:
    raw_transport = server_config.get("transport")
    if isinstance(raw_transport, str) and raw_transport.strip():
        return raw_transport.strip()
    raw_type = server_config.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        normalized_type = raw_type.strip()
        if normalized_type == "local":
            return "stdio"
        if normalized_type == "remote":
            raw_url = server_config.get("url")
            return "sse" if isinstance(raw_url, str) and "/sse" in raw_url else "http"
        return normalized_type
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


def _build_stdio_env(server_config: Mapping[str, JsonValue]) -> dict[str, str]:
    merged_env = dict(os.environ)
    explicit_env = _string_dict(server_config.get("env")) or {}
    merged_env.update(explicit_env)
    return merged_env
