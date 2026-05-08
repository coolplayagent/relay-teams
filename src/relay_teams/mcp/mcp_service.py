# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
import os
import time

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.logger import get_logger
from relay_teams.mcp.mcp_config_manager import McpConfigManager
from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpDiscoveryStatus,
    McpServerAddResult,
    McpServerConfigResult,
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSpec,
    McpServerSummary,
    McpServerToolsSummary,
    McpServerUpdateRequest,
)
from relay_teams.mcp.mcp_registry import McpRegistry

from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)
MCP_TOOL_LOAD_CONCURRENCY_ENV = "RELAY_TEAMS_MCP_TOOL_LOAD_CONCURRENCY"
MCP_TOOL_LOAD_FAILED_TTL_MS_ENV = "RELAY_TEAMS_MCP_TOOL_LOAD_FAILED_TTL_MS"
MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS_ENV = (
    "RELAY_TEAMS_MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS"
)
DEFAULT_MCP_TOOL_LOAD_CONCURRENCY = 2
DEFAULT_MCP_TOOL_LOAD_FAILED_TTL_MS = 60_000
DEFAULT_MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS = 1_000


class McpToolLoadBusyError(RuntimeError):
    pass


class McpToolLoadUnavailableError(RuntimeError):
    pass


class _McpServerToolsCacheEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: McpServerToolsSummary | None = None
    failed_until: float = 0.0


class McpService:
    def __init__(
        self,
        *,
        registry: McpRegistry,
        config_manager: McpConfigManager | None = None,
        on_registry_changed: Callable[[McpRegistry], None] | None = None,
        extra_specs: tuple[McpServerSpec, ...] = (),
        discovery_service: McpDiscoveryService | None = None,
    ) -> None:
        self._registry: McpRegistry = registry
        self._config_manager: McpConfigManager | None = config_manager
        self._on_registry_changed: Callable[[McpRegistry], None] | None = (
            on_registry_changed
        )
        self._extra_specs: tuple[McpServerSpec, ...] = extra_specs
        self._discovery_service: McpDiscoveryService | None = discovery_service
        self._active_tool_load_count = 0
        self._tool_load_cache: dict[str, _McpServerToolsCacheEntry] = {}
        self._global_tool_load_failed_until = 0.0

    def replace_registry(self, registry: McpRegistry) -> None:
        self._registry = registry
        if self._discovery_service is not None:
            self._discovery_service.replace_registry(registry)
        self._clear_tool_load_cache()

    def replace_extra_specs(self, extra_specs: tuple[McpServerSpec, ...]) -> None:
        self._extra_specs = extra_specs
        self._clear_tool_load_cache()

    def _load_registry(self) -> McpRegistry:
        if self._config_manager is None:
            raise RuntimeError("MCP config manager is not available")
        return self._config_manager.load_registry(extra_specs=self._extra_specs)

    def _publish_registry(self, registry: McpRegistry) -> None:
        if self._on_registry_changed is not None:
            self._registry = registry
            self._on_registry_changed(registry)
            return
        self.replace_registry(registry)

    def list_servers(self) -> tuple[McpServerSummary, ...]:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="list_servers",
        ):
            if self._discovery_service is not None:
                return self._discovery_service.list_server_summaries()
            return tuple(
                McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                    discovery_status=(
                        McpDiscoveryStatus.PENDING
                        if spec.enabled
                        else McpDiscoveryStatus.DISABLED
                    ),
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
            config = (
                self._config_manager.get_server_config(name)
                if spec.source == McpConfigScope.APP
                else spec.server_config
            )
            return McpServerConfigResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                    discovery_status=(
                        McpDiscoveryStatus.PENDING
                        if spec.enabled
                        else McpDiscoveryStatus.DISABLED
                    ),
                ),
                config=config,
            )

    async def list_server_tools(self, name: str) -> McpServerToolsSummary:
        normalized_name = name.strip()
        spec = self._registry.get_spec(normalized_name)
        if self._discovery_service is not None:
            return self._discovery_service.get_tools_summary(normalized_name)
        if not spec.enabled:
            return McpServerToolsSummary(
                server=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                enabled=spec.enabled,
                status=McpDiscoveryStatus.DISABLED,
            )
        cached = self._cached_server_tools(normalized_name)
        if cached is not None:
            return cached
        self._raise_if_recent_tool_load_failure(normalized_name)
        self._enter_tool_load_slot(normalized_name)
        try:
            return await self._list_server_tools_uncached(normalized_name, spec)
        except Exception as exc:
            self._remember_tool_load_failure(normalized_name)
            raise McpToolLoadUnavailableError(str(exc)) from exc
        finally:
            self._active_tool_load_count = max(0, self._active_tool_load_count - 1)

    async def _list_server_tools_uncached(
        self,
        name: str,
        spec: McpServerSpec,
    ) -> McpServerToolsSummary:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="list_server_tools",
            attributes={"server_name": name},
        ):
            tools = await self._registry.list_tools(name)
            summary = McpServerToolsSummary(
                server=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                enabled=spec.enabled,
                tools=tools,
                status=McpDiscoveryStatus.READY,
            )
            self._registry.mark_server_runtime_available(name)
            self._tool_load_cache[name] = _McpServerToolsCacheEntry(summary=summary)
            return summary

    def refresh_server_tools(self, name: str) -> McpServerToolsSummary:
        with trace_span(
            LOGGER,
            component="mcp.service",
            operation="refresh_server_tools",
            attributes={"server_name": name},
        ):
            normalized_name = name.strip()
            self._registry.get_spec(normalized_name)
            if self._discovery_service is not None:
                return self._discovery_service.refresh_server(normalized_name)
            self._tool_load_cache.pop(normalized_name, None)
            self._registry.mark_server_runtime_available(normalized_name)
            self._global_tool_load_failed_until = 0.0
            spec = self._registry.get_spec(normalized_name)
            return McpServerToolsSummary(
                server=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                enabled=spec.enabled,
                status=(
                    McpDiscoveryStatus.PENDING
                    if spec.enabled
                    else McpDiscoveryStatus.DISABLED
                ),
            )

    def _cached_server_tools(self, name: str) -> McpServerToolsSummary | None:
        cached = self._tool_load_cache.get(name)
        if cached is None:
            return None
        return cached.summary

    def _raise_if_recent_tool_load_failure(self, name: str) -> None:
        cached = self._tool_load_cache.get(name)
        now = time.monotonic()
        if cached is not None and cached.failed_until > now:
            raise McpToolLoadBusyError(f"MCP server '{name}' failed recently")
        if self._global_tool_load_failed_until > now:
            raise McpToolLoadBusyError(
                f"MCP tool loading is cooling down; retry loading '{name}' later"
            )
        if self._registry.is_server_runtime_failed(name):
            self._registry.mark_server_runtime_available(name)

    def _enter_tool_load_slot(self, name: str) -> None:
        limit = _non_negative_int_env(
            MCP_TOOL_LOAD_CONCURRENCY_ENV,
            DEFAULT_MCP_TOOL_LOAD_CONCURRENCY,
        )
        if limit < 1 or self._active_tool_load_count >= limit:
            raise McpToolLoadBusyError(
                f"MCP tool loading is busy; retry loading '{name}' later"
            )
        self._active_tool_load_count += 1

    def _remember_tool_load_failure(self, name: str) -> None:
        ttl_seconds = (
            _positive_int_env(
                MCP_TOOL_LOAD_FAILED_TTL_MS_ENV,
                DEFAULT_MCP_TOOL_LOAD_FAILED_TTL_MS,
            )
            / 1000.0
        )
        self._registry.mark_server_runtime_failed(name)
        self._tool_load_cache[name] = _McpServerToolsCacheEntry(
            failed_until=time.monotonic() + ttl_seconds,
        )
        global_ttl_seconds = (
            _non_negative_int_env(
                MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS_ENV,
                DEFAULT_MCP_TOOL_LOAD_GLOBAL_FAILURE_TTL_MS,
            )
            / 1000.0
        )
        if global_ttl_seconds > 0:
            self._global_tool_load_failed_until = max(
                self._global_tool_load_failed_until,
                time.monotonic() + global_ttl_seconds,
            )

    def _clear_tool_load_cache(self) -> None:
        self._tool_load_cache.clear()
        self._active_tool_load_count = 0
        self._global_tool_load_failed_until = 0.0

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
            normalized_name = name.strip()
            if normalized_name:
                self._require_no_non_app_shadow(normalized_name)
            config_path = self._config_manager.add_server(
                name=name,
                server_config=server_config,
                overwrite=overwrite,
            )
            self._publish_registry(self._load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerAddResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                    discovery_status=(
                        McpDiscoveryStatus.PENDING
                        if spec.enabled
                        else McpDiscoveryStatus.DISABLED
                    ),
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
            self._require_app_managed_server(name)
            self._config_manager.set_server_enabled(
                name=name,
                enabled=request.enabled,
            )
            self._publish_registry(self._load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerSummary(
                name=spec.name,
                source=spec.source,
                transport=_detect_transport(spec.server_config),
                enabled=spec.enabled,
                discovery_status=(
                    McpDiscoveryStatus.PENDING
                    if spec.enabled
                    else McpDiscoveryStatus.DISABLED
                ),
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
            self._require_app_managed_server(name)
            self._config_manager.update_server(
                name=name,
                server_config=request.config,
            )
            self._publish_registry(self._load_registry())
            spec = self._registry.get_spec(name.strip())
            return McpServerConfigResult(
                server=McpServerSummary(
                    name=spec.name,
                    source=spec.source,
                    transport=_detect_transport(spec.server_config),
                    enabled=spec.enabled,
                    discovery_status=(
                        McpDiscoveryStatus.PENDING
                        if spec.enabled
                        else McpDiscoveryStatus.DISABLED
                    ),
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
                if self._discovery_service is not None:
                    self._discovery_service.mark_failed(name, exc)
                return McpServerConnectionTestResult(
                    server=spec.name,
                    source=spec.source,
                    transport=transport,
                    enabled=spec.enabled,
                    ok=False,
                    error=str(exc),
                )
            if self._discovery_service is not None:
                self._discovery_service.mark_ready(name, tools)
            return McpServerConnectionTestResult(
                server=spec.name,
                source=spec.source,
                transport=transport,
                enabled=spec.enabled,
                ok=True,
                tool_count=len(tools),
                tools=tools,
            )

    def _require_app_managed_server(self, name: str) -> None:
        spec = self._registry.get_spec(name.strip())
        if spec.source != McpConfigScope.APP:
            raise ValueError(
                f"MCP server is managed by {spec.source.value} and cannot be modified: "
                f"{spec.name}"
            )

    def _require_no_non_app_shadow(self, name: str) -> None:
        try:
            spec = self._registry.get_spec(name)
        except ValueError:
            return
        if spec.source != McpConfigScope.APP:
            raise ValueError(
                f"MCP server is managed by {spec.source.value} and cannot be shadowed "
                f"by app config: {spec.name}"
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


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _non_negative_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed >= 0 else default
