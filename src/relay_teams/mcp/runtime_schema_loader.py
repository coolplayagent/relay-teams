# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from enum import Enum
import logging
import os
from threading import Lock
import time

from pydantic import BaseModel, ConfigDict

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry

LOGGER = get_logger(__name__)

RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV = "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS"
RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV = "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS"
RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS"
)
RUNTIME_MCP_SCHEMA_MAX_CONCURRENCY_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_MAX_CONCURRENCY"
)
RUNTIME_MCP_SCHEMA_GLOBAL_FAILURE_THRESHOLD_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_GLOBAL_FAILURE_THRESHOLD"
)
RUNTIME_MCP_SCHEMA_GLOBAL_COOLDOWN_MS_ENV = (
    "RELAY_TEAMS_RUNTIME_MCP_SCHEMA_GLOBAL_COOLDOWN_MS"
)

DEFAULT_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS = 30_000
DEFAULT_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS = 60_000
DEFAULT_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS = 1_500
DEFAULT_RUNTIME_MCP_SCHEMA_MAX_CONCURRENCY = 3
DEFAULT_RUNTIME_MCP_SCHEMA_GLOBAL_FAILURE_THRESHOLD = 3
DEFAULT_RUNTIME_MCP_SCHEMA_GLOBAL_COOLDOWN_MS = 2_000


class RuntimeMcpSchemaStatus(str, Enum):
    LOADED = "loaded"
    CACHE_HIT = "cache_hit"
    FAILED = "failed"
    SERVER_COOLDOWN = "server_cooldown"
    GLOBAL_COOLDOWN = "global_cooldown"


class RuntimeMcpServerSchemaResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_name: str
    status: RuntimeMcpSchemaStatus
    schemas: tuple[McpToolSchema, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (
            RuntimeMcpSchemaStatus.LOADED,
            RuntimeMcpSchemaStatus.CACHE_HIT,
        )


class RuntimeMcpSchemaLoadResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[RuntimeMcpServerSchemaResult, ...]
    schemas_by_server: dict[str, tuple[McpToolSchema, ...]]

    @property
    def loaded_count(self) -> int:
        return sum(1 for result in self.results if result.ok)

    @property
    def skipped_count(self) -> int:
        return len(self.results) - self.loaded_count


class _CachedRuntimeMcpSchemas(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemas: tuple[McpToolSchema, ...]
    expires_at: float


class _RuntimeMcpServerFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error: str
    failed_until: float


class RuntimeMcpSchemaLoader:
    def __init__(
        self,
        registry: McpRegistry,
        *,
        cache_ttl_seconds: float | None = None,
        failure_ttl_seconds: float | None = None,
        server_timeout_seconds: float | None = None,
        max_concurrency: int | None = None,
        global_failure_threshold: int | None = None,
        global_cooldown_seconds: float | None = None,
    ) -> None:
        self._registry = registry
        self._cache_ttl_seconds = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_CACHE_TTL_MS,
            )
            / 1000.0
        )
        self._failure_ttl_seconds = (
            failure_ttl_seconds
            if failure_ttl_seconds is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_FAILED_TTL_MS,
            )
            / 1000.0
        )
        self._server_timeout_seconds = (
            server_timeout_seconds
            if server_timeout_seconds is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS,
            )
            / 1000.0
        )
        resolved_max_concurrency = (
            max_concurrency
            if max_concurrency is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_MAX_CONCURRENCY_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_MAX_CONCURRENCY,
            )
        )
        self._global_failure_threshold = (
            global_failure_threshold
            if global_failure_threshold is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_GLOBAL_FAILURE_THRESHOLD_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_GLOBAL_FAILURE_THRESHOLD,
            )
        )
        self._global_cooldown_seconds = (
            global_cooldown_seconds
            if global_cooldown_seconds is not None
            else _positive_int_env(
                RUNTIME_MCP_SCHEMA_GLOBAL_COOLDOWN_MS_ENV,
                DEFAULT_RUNTIME_MCP_SCHEMA_GLOBAL_COOLDOWN_MS,
            )
            / 1000.0
        )
        self._semaphore = asyncio.Semaphore(max(1, resolved_max_concurrency))
        self._lock = Lock()
        self._cache: dict[str, _CachedRuntimeMcpSchemas] = {}
        self._failures: dict[str, _RuntimeMcpServerFailure] = {}
        self._in_flight: dict[str, asyncio.Task[RuntimeMcpServerSchemaResult]] = {}
        self._consecutive_failure_count = 0
        self._global_cooldown_until = 0.0
        self._registry_generation = 0

    def replace_registry(self, registry: McpRegistry) -> None:
        with self._lock:
            self._registry = registry
            self._registry_generation += 1
            self._cache.clear()
            self._failures.clear()
            self._in_flight.clear()
            self._consecutive_failure_count = 0
            self._global_cooldown_until = 0.0

    def invalidate_server(self, server_name: str) -> None:
        normalized_name = server_name.strip()
        with self._lock:
            if normalized_name in self._cache:
                del self._cache[normalized_name]
            if normalized_name in self._failures:
                del self._failures[normalized_name]

    async def load_many(
        self,
        server_names: tuple[str, ...],
        *,
        force: bool = False,
    ) -> RuntimeMcpSchemaLoadResult:
        results = await asyncio.gather(
            *(
                self.load_server(server_name, force=force)
                for server_name in server_names
            )
        )
        schemas_by_server = {
            result.server_name: result.schemas for result in results if result.ok
        }
        load_result = RuntimeMcpSchemaLoadResult(
            results=tuple(results),
            schemas_by_server=schemas_by_server,
        )
        self._log_load_result(load_result)
        return load_result

    async def load_server(
        self,
        server_name: str,
        *,
        force: bool = False,
    ) -> RuntimeMcpServerSchemaResult:
        normalized_name = server_name.strip()
        if not normalized_name:
            return RuntimeMcpServerSchemaResult(
                server_name=server_name,
                status=RuntimeMcpSchemaStatus.FAILED,
                error="MCP server name is empty",
            )
        current_loop = asyncio.get_running_loop()
        task_to_await: asyncio.Task[RuntimeMcpServerSchemaResult] | None = None
        created_task: asyncio.Task[RuntimeMcpServerSchemaResult] | None = None
        with self._lock:
            registry = self._registry
            registry_generation = self._registry_generation
            if not force:
                cached_result = self._cached_result_locked(normalized_name)
                if cached_result is not None:
                    return cached_result
                cooldown_result = self._cooldown_result_locked(normalized_name)
                if cooldown_result is not None:
                    return cooldown_result
            existing_task = self._in_flight.get(normalized_name)
            if existing_task is not None:
                if existing_task.done():
                    del self._in_flight[normalized_name]
                elif existing_task.get_loop() == current_loop:
                    task_to_await = existing_task
            if task_to_await is None:
                created_task = current_loop.create_task(
                    self._load_uncached_server(
                        normalized_name,
                        registry=registry,
                        registry_generation=registry_generation,
                    )
                )
                self._in_flight[normalized_name] = created_task
                task_to_await = created_task

        try:
            return await asyncio.shield(task_to_await)
        finally:
            if created_task is not None:
                with self._lock:
                    if self._in_flight.get(normalized_name) is created_task:
                        del self._in_flight[normalized_name]

    async def _load_uncached_server(
        self,
        server_name: str,
        *,
        registry: McpRegistry,
        registry_generation: int,
    ) -> RuntimeMcpServerSchemaResult:
        try:
            async with self._semaphore:
                with self._lock:
                    global_cooldown_result = self._global_cooldown_result_locked(
                        server_name
                    )
                    if global_cooldown_result is not None:
                        return global_cooldown_result
                schemas = await asyncio.wait_for(
                    registry.list_tool_schemas(server_name),
                    timeout=self._server_timeout_seconds,
                )
        except TimeoutError as exc:
            return self._remember_failure(
                server_name,
                "timed out",
                exc,
                registry=registry,
                registry_generation=registry_generation,
            )
        except Exception as exc:
            return self._remember_failure(
                server_name,
                _exception_message(exc),
                exc,
                registry=registry,
                registry_generation=registry_generation,
            )

        now = time.monotonic()
        with self._lock:
            if (
                registry_generation != self._registry_generation
                or registry is not self._registry
            ):
                return RuntimeMcpServerSchemaResult(
                    server_name=server_name,
                    status=RuntimeMcpSchemaStatus.FAILED,
                    error=(
                        f"MCP server {server_name} schema load was discarded "
                        "because the MCP registry was replaced"
                    ),
                )
            self._cache[server_name] = _CachedRuntimeMcpSchemas(
                schemas=schemas,
                expires_at=now + self._cache_ttl_seconds,
            )
            if server_name in self._failures:
                del self._failures[server_name]
            self._consecutive_failure_count = 0
            self._global_cooldown_until = 0.0
        registry.mark_server_runtime_available(server_name)
        return RuntimeMcpServerSchemaResult(
            server_name=server_name,
            status=RuntimeMcpSchemaStatus.LOADED,
            schemas=schemas,
        )

    def _remember_failure(
        self,
        server_name: str,
        error: str,
        exc: Exception,
        *,
        registry: McpRegistry,
        registry_generation: int,
    ) -> RuntimeMcpServerSchemaResult:
        now = time.monotonic()
        failed_until = now + self._failure_ttl_seconds
        with self._lock:
            if (
                registry_generation != self._registry_generation
                or registry is not self._registry
            ):
                return RuntimeMcpServerSchemaResult(
                    server_name=server_name,
                    status=RuntimeMcpSchemaStatus.FAILED,
                    error=(
                        f"MCP server {server_name} schema load was discarded "
                        "because the MCP registry was replaced"
                    ),
                )
            if server_name in self._cache:
                del self._cache[server_name]
            self._failures[server_name] = _RuntimeMcpServerFailure(
                error=_format_server_error(server_name, error),
                failed_until=failed_until,
            )
            self._consecutive_failure_count += 1
            if self._consecutive_failure_count >= self._global_failure_threshold:
                self._global_cooldown_until = now + self._global_cooldown_seconds
        registry.mark_server_runtime_failed(server_name)
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.runtime_schema_loader.failed",
            message="MCP runtime schema load failed",
            payload={"server_name": server_name, "error": error},
            exc_info=exc,
        )
        return RuntimeMcpServerSchemaResult(
            server_name=server_name,
            status=RuntimeMcpSchemaStatus.FAILED,
            error=_format_server_error(server_name, error),
        )

    def _cached_result_locked(
        self,
        server_name: str,
    ) -> RuntimeMcpServerSchemaResult | None:
        cached = self._cache.get(server_name)
        if cached is None:
            return None
        if cached.expires_at <= time.monotonic():
            del self._cache[server_name]
            return None
        return RuntimeMcpServerSchemaResult(
            server_name=server_name,
            status=RuntimeMcpSchemaStatus.CACHE_HIT,
            schemas=cached.schemas,
        )

    def _cooldown_result_locked(
        self,
        server_name: str,
    ) -> RuntimeMcpServerSchemaResult | None:
        global_cooldown_result = self._global_cooldown_result_locked(server_name)
        if global_cooldown_result is not None:
            return global_cooldown_result
        now = time.monotonic()
        failure = self._failures.get(server_name)
        if failure is None:
            return None
        if failure.failed_until <= now:
            del self._failures[server_name]
            return None
        return RuntimeMcpServerSchemaResult(
            server_name=server_name,
            status=RuntimeMcpSchemaStatus.SERVER_COOLDOWN,
            error=f"MCP server {server_name} is in failure cooldown: {failure.error}",
        )

    def _global_cooldown_result_locked(
        self,
        server_name: str,
    ) -> RuntimeMcpServerSchemaResult | None:
        now = time.monotonic()
        if self._global_cooldown_until > now:
            return RuntimeMcpServerSchemaResult(
                server_name=server_name,
                status=RuntimeMcpSchemaStatus.GLOBAL_COOLDOWN,
                error=(
                    "MCP schema loading is in global cooldown after repeated "
                    f"failures; skipped server {server_name}"
                ),
            )
        return None

    @staticmethod
    def _log_load_result(result: RuntimeMcpSchemaLoadResult) -> None:
        if result.skipped_count == 0:
            return
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.runtime_schema_loader.degraded",
            message="Runtime MCP schema load skipped unavailable servers",
            payload={
                "loaded_count": result.loaded_count,
                "skipped_count": result.skipped_count,
                "skipped_server_names": [
                    entry.server_name for entry in result.results if not entry.ok
                ],
            },
        )


def _format_server_error(server_name: str, error: str) -> str:
    stripped_error = error.strip()
    if not stripped_error:
        stripped_error = "unknown error"
    return f"MCP server {server_name} schema load failed: {stripped_error}"


def _exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default
