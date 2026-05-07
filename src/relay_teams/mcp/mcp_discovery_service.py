# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
import hashlib
from json import dumps
import logging

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpDiscoveryStatus,
    McpServerSpec,
    McpServerSummary,
    McpServerToolsSummary,
    McpToolInfo,
)
from relay_teams.mcp.mcp_registry import McpRegistry

LOGGER = get_logger(__name__)
_DEFAULT_DISCOVERY_CONCURRENCY = 3


class McpDiscoveryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    source: McpConfigScope
    transport: str
    enabled: bool
    status: McpDiscoveryStatus
    tools: tuple[McpToolInfo, ...] = ()
    last_checked_at: datetime | None = None
    error: str | None = None
    generation: int
    fingerprint: str


class McpDiscoveryService:
    def __init__(
        self,
        registry: McpRegistry,
        *,
        max_concurrency: int = _DEFAULT_DISCOVERY_CONCURRENCY,
    ) -> None:
        self._registry = registry
        self._generation = 0
        self._records: dict[str, McpDiscoveryRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._reset_records(registry)

    def start_warmup(self, registry: McpRegistry) -> None:
        self._bind_current_loop()
        self.replace_registry(registry)

    def replace_registry(self, registry: McpRegistry) -> None:
        if self._should_defer_to_bound_loop():
            loop = self._loop
            if loop is not None:
                loop.call_soon_threadsafe(self._replace_registry_now, registry)
            return
        self._bind_current_loop()
        self._replace_registry_now(registry)

    def _replace_registry_now(self, registry: McpRegistry) -> None:
        self._registry = registry
        self._generation += 1
        next_records: dict[str, McpDiscoveryRecord] = {}
        specs_to_enqueue: list[McpServerSpec] = []
        seen_names: set[str] = set()

        for spec in registry.list_specs():
            seen_names.add(spec.name)
            existing = self._records.get(spec.name)
            fingerprint = _fingerprint_spec(
                spec,
                registry.discovery_fingerprint_context(),
            )
            if not spec.enabled:
                self._cancel_task(spec.name)
                next_records[spec.name] = self._record_from_spec(
                    spec,
                    generation=self._generation,
                )
                continue

            if existing is not None and existing.fingerprint == fingerprint:
                next_records[spec.name] = existing.model_copy(
                    update={
                        "source": spec.source,
                        "transport": _detect_transport(spec.server_config),
                        "enabled": spec.enabled,
                        "generation": self._generation,
                    }
                )
                if existing.status == McpDiscoveryStatus.PENDING:
                    specs_to_enqueue.append(spec)
                if (
                    existing.status == McpDiscoveryStatus.LOADING
                    and not self._has_active_task(spec.name)
                ):
                    specs_to_enqueue.append(spec)
                continue

            self._cancel_task(spec.name)
            next_records[spec.name] = self._record_from_spec(
                spec,
                generation=self._generation,
            )
            specs_to_enqueue.append(spec)

        for removed_name in set(self._records).difference(seen_names):
            self._cancel_task(removed_name)

        self._records = next_records
        self._enqueue_enabled_servers(registry, specs_to_enqueue, force=False)

    async def close(self) -> None:
        self._cancel_pending_tasks()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def list_server_summaries(self) -> tuple[McpServerSummary, ...]:
        return tuple(
            McpServerSummary(
                name=record.name,
                source=record.source,
                transport=record.transport,
                enabled=record.enabled,
                discovery_status=record.status,
                tool_count=len(record.tools),
                last_checked_at=record.last_checked_at,
                error=record.error,
            )
            for record in self._sorted_records()
        )

    def get_tools_summary(self, name: str) -> McpServerToolsSummary:
        normalized_name = name.strip()
        record = self._records.get(normalized_name)
        if record is None:
            spec = self._registry.get_spec(normalized_name)
            record = self._record_from_spec(spec, generation=self._generation)
            self._records[spec.name] = record
        return self._tools_summary_from_record(record)

    def get_ready_tools(self, name: str) -> tuple[McpToolInfo, ...]:
        summary = self.get_tools_summary(name)
        if summary.status != McpDiscoveryStatus.READY:
            return ()
        return summary.tools

    def is_ready(self, name: str) -> bool:
        return self.get_tools_summary(name).status == McpDiscoveryStatus.READY

    def refresh_server(self, name: str) -> McpServerToolsSummary:
        normalized_name = name.strip()
        spec = self._registry.get_spec(normalized_name)
        record = self._record_from_spec(spec, generation=self._generation)
        if not spec.enabled:
            self._records[spec.name] = record
            return self._tools_summary_from_record(record)
        self._records[spec.name] = record.model_copy(
            update={"status": McpDiscoveryStatus.LOADING, "error": None}
        )
        self._schedule_discovery(
            self._registry,
            spec,
            fingerprint=record.fingerprint,
            force=True,
        )
        return self.get_tools_summary(spec.name)

    def mark_ready(self, name: str, tools: tuple[McpToolInfo, ...]) -> None:
        normalized_name = name.strip()
        self._cancel_task(normalized_name)
        self._registry.mark_server_runtime_available(normalized_name)
        record = self._records.get(normalized_name)
        if record is None:
            spec = self._registry.get_spec(normalized_name)
            record = self._record_from_spec(spec, generation=self._generation)
        self._records[normalized_name] = record.model_copy(
            update={
                "status": McpDiscoveryStatus.READY,
                "tools": tools,
                "last_checked_at": _now(),
                "error": None,
                "generation": self._generation,
            }
        )

    def _reset_records(self, registry: McpRegistry) -> None:
        self._records = {
            spec.name: self._record_from_spec(spec, generation=self._generation)
            for spec in registry.list_specs()
        }

    def _enqueue_enabled_servers(
        self,
        registry: McpRegistry,
        specs: Iterable[McpServerSpec],
        *,
        force: bool,
    ) -> None:
        for spec in specs:
            if spec.enabled:
                record = self._records.get(spec.name)
                fingerprint = (
                    record.fingerprint
                    if record is not None
                    else _fingerprint_spec(
                        spec,
                        registry.discovery_fingerprint_context(),
                    )
                )
                self._schedule_discovery(
                    registry,
                    spec,
                    fingerprint=fingerprint,
                    force=force,
                )

    def _schedule_discovery(
        self,
        registry: McpRegistry,
        spec: McpServerSpec,
        *,
        fingerprint: str,
        force: bool,
    ) -> None:
        name = spec.name
        existing_task = self._tasks.get(name)
        if existing_task is not None and not existing_task.done():
            existing_record = self._records.get(name)
            if (
                not force
                and existing_record is not None
                and existing_record.fingerprint == fingerprint
            ):
                return
            existing_task.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        record = self._records.get(name)
        if record is not None:
            self._records[name] = record.model_copy(
                update={"status": McpDiscoveryStatus.LOADING, "error": None}
            )
        task = loop.create_task(self._discover_server(registry, name, fingerprint))
        self._tasks[name] = task
        task.add_done_callback(
            lambda completed_task: self._discard_task(name, completed_task)
        )

    async def _discover_server(
        self,
        registry: McpRegistry,
        name: str,
        fingerprint: str,
    ) -> None:
        try:
            async with self._semaphore:
                if not self._task_is_current(name):
                    return
                if not self._record_fingerprint_matches(name, fingerprint):
                    return
                tools = await registry.list_tools_for_discovery(name)
                if not self._task_is_current(name):
                    return
                if not self._record_fingerprint_matches(name, fingerprint):
                    return
                record = self._records.get(name)
                if record is None:
                    return
                registry.mark_server_runtime_available(name)
                self._records[name] = record.model_copy(
                    update={
                        "status": McpDiscoveryStatus.READY,
                        "tools": tools,
                        "last_checked_at": _now(),
                        "error": None,
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._task_is_current(name):
                return
            if not self._record_fingerprint_matches(name, fingerprint):
                return
            self._mark_failed(name, exc)

    def mark_failed(self, name: str, exc: Exception) -> None:
        normalized_name = name.strip()
        self._cancel_task(normalized_name)
        if normalized_name not in self._records:
            spec = self._registry.get_spec(normalized_name)
            self._records[normalized_name] = self._record_from_spec(
                spec,
                generation=self._generation,
            )
        self._mark_failed(normalized_name, exc)

    def _mark_failed(self, name: str, exc: Exception) -> None:
        record = self._records.get(name)
        if record is None:
            return
        error = _format_error(exc)
        self._records[name] = record.model_copy(
            update={
                "status": McpDiscoveryStatus.FAILED,
                "tools": (),
                "last_checked_at": _now(),
                "error": error,
            }
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="mcp.discovery.failed",
            message="MCP tool discovery failed",
            payload={
                "server_name": name,
                "transport": record.transport,
                "source": record.source.value,
                "error": error,
            },
        )

    def _record_from_spec(
        self,
        spec: McpServerSpec,
        *,
        generation: int,
    ) -> McpDiscoveryRecord:
        status = (
            McpDiscoveryStatus.PENDING if spec.enabled else McpDiscoveryStatus.DISABLED
        )
        return McpDiscoveryRecord(
            name=spec.name,
            source=spec.source,
            transport=_detect_transport(spec.server_config),
            enabled=spec.enabled,
            status=status,
            generation=generation,
            fingerprint=_fingerprint_spec(
                spec,
                self._registry.discovery_fingerprint_context(),
            ),
        )

    @staticmethod
    def _tools_summary_from_record(
        record: McpDiscoveryRecord,
    ) -> McpServerToolsSummary:
        return McpServerToolsSummary(
            server=record.name,
            source=record.source,
            transport=record.transport,
            enabled=record.enabled,
            tools=record.tools,
            status=record.status,
            last_checked_at=record.last_checked_at,
            error=record.error,
        )

    def _sorted_records(self) -> tuple[McpDiscoveryRecord, ...]:
        return tuple(self._records[name] for name in sorted(self._records))

    def _cancel_pending_tasks(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

    def _cancel_task(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()

    def _discard_task(self, name: str, completed_task: asyncio.Task[None]) -> None:
        if self._tasks.get(name) is completed_task:
            del self._tasks[name]

    def _has_active_task(self, name: str) -> bool:
        task = self._tasks.get(name)
        return task is not None and not task.done()

    def _record_fingerprint_matches(self, name: str, fingerprint: str) -> bool:
        record = self._records.get(name)
        return record is not None and record.fingerprint == fingerprint

    def _task_is_current(self, name: str) -> bool:
        return self._tasks.get(name) is asyncio.current_task()

    def _bind_current_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._loop = loop

    def _should_defer_to_bound_loop(self) -> bool:
        loop = self._loop
        if loop is None or not loop.is_running():
            return False
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return True
        return current_loop is not loop


def _detect_transport(server_config: Mapping[str, object]) -> str:
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
    raw_command = server_config.get("command")
    if isinstance(raw_command, str) and raw_command.strip():
        return "stdio"
    raw_url = server_config.get("url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "sse" if "/sse" in raw_url else "http"
    return "unknown"


def _fingerprint_spec(
    spec: McpServerSpec,
    discovery_context: Mapping[str, JsonValue],
) -> str:
    payload: dict[str, JsonValue] = {
        "name": spec.name,
        "source": spec.source.value,
        "enabled": spec.enabled,
        "transport": _detect_transport(spec.server_config),
        "server_config": spec.server_config,
        "discovery_context": dict(discovery_context),
    }
    serialized = dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _format_error(exc: Exception) -> str:
    root = _root_exception(exc)
    return f"{type(root).__name__}: {root}"


def _root_exception(exc: BaseException) -> BaseException:
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return _root_exception(exc.exceptions[0])
    return exc


def _now() -> datetime:
    return datetime.now(UTC)
