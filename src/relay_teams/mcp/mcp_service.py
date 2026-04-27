# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from relay_teams.logger import get_logger
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_models import (
    McpServerAddResult,
    McpServerConfigResult,
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSummary,
    McpServerToolsSummary,
    McpServerUpdateRequest,
)
from relay_teams.mcp.mcp_registry import McpRegistry

from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)


class McpService:
    def __init__(
        self,
        *,
        registry: McpRegistry,
        config_manager: McpConfigManager | None = None,
    ) -> None:
        self._registry: McpRegistry = registry
        self._config_manager: McpConfigManager | None = config_manager

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
                    enabled=spec.enabled,
                )
                for spec in self._registry.list_specs()
            )

    def list_enabled_servers(self) -> tuple[McpServerSummary, ...]:
        return tuple(server for server in self.list_servers() if server.enabled)

    def get_server_config(self, name: str) -> McpServerConfigResult:
        if self._config_manager is None:
            raise RuntimeError("MCP config manager is not available")
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="get_server_config",
            attributes={"server_name": name},
        ):
            spec = self._registry.get_spec(name.strip())
            config = self._config_manager.get_server_config(name)
            return McpServerConfigResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                ),
                config=config,
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
                enabled=spec.enabled,
                tools=tools,
            )

    def add_server(
        self,
        *,
        name: str,
        server_config: dict[str, JsonValue],
        overwrite: bool = False,
    ) -> McpServerAddResult:
        if self._config_manager is None:
            raise RuntimeError("MCP config manager is not available")
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="add_server",
            attributes={"server_name": name},
        ):
            config_path = self._config_manager.add_server(
                name=name,
                server_config=server_config,
                overwrite=overwrite,
            )
            self.replace_registry(self._config_manager.load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerAddResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                ),
                config_path=str(config_path),
            )

    def set_server_enabled(
        self,
        name: str,
        request: McpServerEnabledUpdateRequest,
    ) -> McpServerSummary:
        if self._config_manager is None:
            raise RuntimeError("MCP config manager is not available")
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="set_server_enabled",
            attributes={"server_name": name, "enabled": request.enabled},
        ):
            self._config_manager.set_server_enabled(
                name=name,
                enabled=request.enabled,
            )
            self.replace_registry(self._config_manager.load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerSummary(
                name=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                enabled=spec.enabled,
            )

    def update_server(
        self,
        name: str,
        request: McpServerUpdateRequest,
    ) -> McpServerConfigResult:
        if self._config_manager is None:
            raise RuntimeError("MCP config manager is not available")
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="update_server",
            attributes={"server_name": name},
        ):
            self._config_manager.update_server(
                name=name,
                server_config=request.config,
            )
            self.replace_registry(self._config_manager.load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerConfigResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                ),
                config=self._config_manager.get_server_config(name),
            )

    async def test_server_connection(self, name: str) -> McpServerConnectionTestResult:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="test_server_connection",
            attributes={"server_name": name},
        ):
            spec = self._registry.get_spec(name)
            transport = _detect_transport(spec.server_config)
            try:
                tools = await self._registry.list_tools(name)
            except Exception as exc:
                return McpServerConnectionTestResult(
                    server=spec.name,
                    source=spec.source,
                    transport=transport,
                    enabled=spec.enabled,
                    ok=False,
                    error=str(exc),
                )
            return McpServerConnectionTestResult(
                server=spec.name,
                source=spec.source,
                transport=transport,
                enabled=spec.enabled,
                ok=True,
                tool_count=len(tools),
                tools=tools,
            )


def _detect_transport(server_config: dict[str, JsonValue]) -> str:
    raw_transport = server_config.get("transport")
    if isinstance(raw_transport, str) and raw_transport.strip():
        return raw_transport

    raw_type = server_config.get("type")
    if isinstance(raw_type, str) and raw_type.strip():
        normalized_type = raw_type.strip()
        if normalized_type == "local":
            return "stdio"
        if normalized_type == "remote":
            raw_url = server_config.get("url")
            return "sse" if isinstance(raw_url, str) and "/sse" in raw_url else "http"
        return normalized_type

    raw_command = server_config.get("command")
    if isinstance(raw_command, str) and raw_command.strip():
        return "stdio"

    raw_url = server_config.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "sse" if "/sse" in raw_url else "http"

    return "unknown"
