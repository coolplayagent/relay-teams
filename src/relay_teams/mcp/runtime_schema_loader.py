# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock
import time

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry

LOGGER = get_logger(__name__)

RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS_ENV = "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS"
RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS"
)
RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV = "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS"
RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV = "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS"
RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES"
)
MCP_TOOLSET_REQUIRE_READY_SCHEMA_ENV = "RELAY_TEAMS_MCP_TOOLSET_REQUIRE_READY_SCHEMA"

DEFAULT_RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS = 500
DEFAULT_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS = 150
DEFAULT_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS = 30_000
DEFAULT_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS = 60_000
DEFAULT_RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES = 4


class RuntimeMcpSchemaLoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemas_by_server: dict[str, tuple[McpToolSchema, ...]]
    cache_hit_count: int = 0
    timeout_count: int = 0
    failure_count: int = 0
    runtime_failed_skipped_count: int = 0
    budget_skipped_count: int = 0
    probe_skipped_count: int = 0
    resolved_server_count: int = 0
    loaded_server_count: int = 0
    skipped_server_names: tuple[str, ...] = ()

    @property
    def skipped_count(self) -> int:
        return (
            self.timeout_count
            + self.failure_count
            + self.runtime_failed_skipped_count
            + self.budget_skipped_count
            + self.probe_skipped_count
        )


class _CachedRuntimeMcpSchemas(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemas: tuple[McpToolSchema, ...] = ()
    expires_at: float = 0.0
    failed_until: float = 0.0


class _RuntimeMcpSchemaLoadAccumulator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemas_by_server: dict[str, tuple[McpToolSchema, ...]] = Field(
        default_factory=dict
    )
    cache_hit_count: int = 0
    timeout_count: int = 0
    failure_count: int = 0
    runtime_failed_skipped_count: int = 0
    budget_skipped_count: int = 0
    probe_skipped_count: int = 0
    loaded_server_count: int = 0
    uncached_probe_count: int = 0
    skipped_server_names: list[str] = Field(default_factory=list)

    def to_result(self, resolved_server_count: int) -> RuntimeMcpSchemaLoadResult:
        return RuntimeMcpSchemaLoadResult(
            schemas_by_server=dict(self.schemas_by_server),
            cache_hit_count=self.cache_hit_count,
            timeout_count=self.timeout_count,
            failure_count=self.failure_count,
            runtime_failed_skipped_count=self.runtime_failed_skipped_count,
            budget_skipped_count=self.budget_skipped_count,
            probe_skipped_count=self.probe_skipped_count,
            resolved_server_count=resolved_server_count,
            loaded_server_count=self.loaded_server_count,
            skipped_server_names=tuple(self.skipped_server_names[:10]),
        )


_CACHE_LOCK = Lock()
_SCHEMA_CACHE: dict[tuple[int, str], _CachedRuntimeMcpSchemas] = {}


async def load_runtime_mcp_tool_schemas(  # pragma: no cover
    *,
    mcp_registry: McpRegistry,
    server_names: tuple[str, ...],
) -> RuntimeMcpSchemaLoadResult:
    accumulator = _RuntimeMcpSchemaLoadAccumulator()
    if not server_names:
        return accumulator.to_result(resolved_server_count=0)

    budget_seconds = (
        _positive_int_env(
            RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS_ENV,
            DEFAULT_RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS,
        )
        / 1000.0
    )
    server_timeout_seconds = (
        _positive_int_env(
            RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV,
            DEFAULT_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS,
        )
        / 1000.0
    )
    max_uncached_probes = _non_negative_int_env(
        RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV,
        DEFAULT_RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES,
    )
    if max_uncached_probes == 0:
        return _load_runtime_mcp_tool_schemas_from_cache_only(
            mcp_registry=mcp_registry,
            server_names=server_names,
        )
    deadline = time.monotonic() + budget_seconds

    for server_name in server_names:
        cached = _get_cached_runtime_mcp_schemas(mcp_registry, server_name)
        if cached is not None:
            accumulator.schemas_by_server[server_name] = cached
            accumulator.cache_hit_count += 1
            accumulator.loaded_server_count += 1
            continue

        if _is_runtime_failed_or_cached_failed(mcp_registry, server_name):
            accumulator.runtime_failed_skipped_count += 1
            accumulator.skipped_server_names.append(server_name)
            continue

        if accumulator.uncached_probe_count >= max_uncached_probes:
            accumulator.probe_skipped_count += 1
            accumulator.skipped_server_names.append(server_name)
            continue

        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            accumulator.budget_skipped_count += 1
            accumulator.skipped_server_names.append(server_name)
            continue

        timeout_seconds = min(server_timeout_seconds, remaining_seconds)
        accumulator.uncached_probe_count += 1
        try:
            schemas = await asyncio.wait_for(
                mcp_registry.list_tool_schemas(server_name),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            mcp_registry.mark_server_runtime_failed(server_name)
            _remember_runtime_mcp_failure(mcp_registry, server_name)
            accumulator.timeout_count += 1
            accumulator.skipped_server_names.append(server_name)
            continue
        except (OSError, RuntimeError, ValueError):
            mcp_registry.mark_server_runtime_failed(server_name)
            _remember_runtime_mcp_failure(mcp_registry, server_name)
            accumulator.failure_count += 1
            accumulator.skipped_server_names.append(server_name)
            continue

        mcp_registry.mark_server_runtime_available(server_name)
        _remember_runtime_mcp_schemas(mcp_registry, server_name, schemas)
        accumulator.schemas_by_server[server_name] = schemas
        accumulator.loaded_server_count += 1

    result = accumulator.to_result(resolved_server_count=len(server_names))
    return result


def _load_runtime_mcp_tool_schemas_from_cache_only(  # pragma: no cover
    *,
    mcp_registry: McpRegistry,
    server_names: tuple[str, ...],
) -> RuntimeMcpSchemaLoadResult:
    accumulator = _RuntimeMcpSchemaLoadAccumulator()
    now = time.monotonic()
    with _CACHE_LOCK:
        for server_name in server_names:
            cached = _SCHEMA_CACHE.get((id(mcp_registry), server_name))
            if cached is None or cached.expires_at <= now:
                accumulator.probe_skipped_count += 1
                accumulator.skipped_server_names.append(server_name)
                continue
            if cached.failed_until > now:
                accumulator.runtime_failed_skipped_count += 1
                accumulator.skipped_server_names.append(server_name)
                continue
            accumulator.schemas_by_server[server_name] = cached.schemas
            accumulator.cache_hit_count += 1
            accumulator.loaded_server_count += 1
    result = accumulator.to_result(resolved_server_count=len(server_names))
    _log_runtime_mcp_load_result(result)
    return result


def _is_runtime_failed_or_cached_failed(
    mcp_registry: McpRegistry,
    server_name: str,
) -> bool:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _SCHEMA_CACHE.get((id(mcp_registry), server_name))
        if cached is not None and cached.failed_until > now:
            return True
    if mcp_registry.is_server_runtime_failed(server_name):
        mcp_registry.mark_server_runtime_available(server_name)
    return False


def cached_runtime_mcp_server_names(
    *,
    mcp_registry: McpRegistry,
    server_names: tuple[str, ...],
) -> tuple[str, ...]:
    """Return MCP servers whose runtime schemas are already warm in memory."""
    now = time.monotonic()
    ready_server_names: list[str] = []
    with _CACHE_LOCK:
        for server_name in server_names:
            cached = _SCHEMA_CACHE.get((id(mcp_registry), server_name))
            if cached is None:
                continue
            if cached.expires_at > now and cached.schemas:
                ready_server_names.append(server_name)
    return tuple(ready_server_names)


def should_require_ready_mcp_toolsets(
    *,
    requested_server_names: tuple[str, ...],
    resolved_server_count: int,
) -> bool:
    """Decide whether agent construction should only attach warm MCP toolsets."""
    env_value = _bool_env(MCP_TOOLSET_REQUIRE_READY_SCHEMA_ENV)
    if env_value is not None:
        return env_value
    max_uncached_probes = _non_negative_int_env(
        RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV,
        DEFAULT_RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES,
    )
    return "*" in requested_server_names or resolved_server_count > max_uncached_probes


def _get_cached_runtime_mcp_schemas(
    mcp_registry: McpRegistry,
    server_name: str,
) -> tuple[McpToolSchema, ...] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _SCHEMA_CACHE.get((id(mcp_registry), server_name))
        if cached is None or cached.expires_at <= now:
            return None
        return cached.schemas


def _remember_runtime_mcp_schemas(
    mcp_registry: McpRegistry,
    server_name: str,
    schemas: tuple[McpToolSchema, ...],
) -> None:
    ttl_seconds = (
        _positive_int_env(
            RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV,
            DEFAULT_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS,
        )
        / 1000.0
    )
    with _CACHE_LOCK:
        _SCHEMA_CACHE[(id(mcp_registry), server_name)] = _CachedRuntimeMcpSchemas(
            schemas=schemas,
            expires_at=time.monotonic() + ttl_seconds,
        )


def _remember_runtime_mcp_failure(
    mcp_registry: McpRegistry,
    server_name: str,
) -> None:
    ttl_seconds = (
        _positive_int_env(
            RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV,
            DEFAULT_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS,
        )
        / 1000.0
    )
    with _CACHE_LOCK:
        _SCHEMA_CACHE[(id(mcp_registry), server_name)] = _CachedRuntimeMcpSchemas(
            failed_until=time.monotonic() + ttl_seconds,
        )


def _log_runtime_mcp_load_result(result: RuntimeMcpSchemaLoadResult) -> None:
    if result.skipped_count <= 0:
        return
    log_event(
        LOGGER,
        logging.WARNING,
        event="orchestration.runtime_tools.mcp_snapshot_degraded",
        message=(
            "Runtime MCP tool snapshot skipped unavailable servers within the "
            "startup budget"
        ),
        payload={
            "resolved_server_count": result.resolved_server_count,
            "loaded_server_count": result.loaded_server_count,
            "cache_hit_count": result.cache_hit_count,
            "timeout_count": result.timeout_count,
            "failure_count": result.failure_count,
            "runtime_failed_skipped_count": result.runtime_failed_skipped_count,
            "budget_skipped_count": result.budget_skipped_count,
            "probe_skipped_count": result.probe_skipped_count,
            "sample_skipped_server_names": list(result.skipped_server_names),
        },
    )


def _positive_int_env(name: str, default: int) -> int:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _non_negative_int_env(name: str, default: int) -> int:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def _bool_env(name: str) -> bool | None:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None:
        return None
    normalized_value = raw_value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    return None
