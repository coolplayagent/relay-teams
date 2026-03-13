# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from agent_teams.logger import get_logger
from agent_teams.mcp.models import McpServerSummary, McpServerToolsSummary
from agent_teams.mcp.registry import McpRegistry

from agent_teams.trace import trace_span

LOGGER = get_logger(__name__)


class McpService:
    def __init__(self, *, registry: McpRegistry) -> None:
        self._registry: McpRegistry = registry

    def replace_registry(self, registry: McpRegistry) -> None:
        self._registry = registry

    def list_servers(self) -> tuple[McpServerSummary, ...]:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="list_servers",
        ):
            return tuple(
                McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                )
                for spec in self._registry.list_specs()
            )

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="list_server_tools",
            attributes={"server_name": name},
        ):
            spec = self._registry.get_spec(name)
            tools = await self._registry.list_tools(name)
            return McpServerToolsSummary(
                server=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                tools=tools,
            )


def _detect_transport(server_config: dict[str, JsonValue]) -> str:
    raw_transport = server_config.get("transport")
    if isinstance(raw_transport, str) and raw_transport.strip():
        return raw_transport

    raw_type = server_config.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        return raw_type

    raw_command = server_config.get("command")
    if isinstance(raw_command, str) and raw_command.strip():
        return "stdio"

    raw_url = server_config.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "sse" if "/sse" in raw_url else "http"

    return "unknown"
