# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from threading import Lock
from time import monotonic
from typing import Any, cast

from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.logger import get_logger, log_event
from relay_teams.media import ContentPart
from relay_teams.providers.token_usage_repo import RunTokenUsage, SessionTokenUsage
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRecord
from relay_teams.sessions.session_list_cache import (
    LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
)
from relay_teams.sessions.session_models import SessionMode
from relay_teams.sessions.session_rounds_projection import (
    approvals_to_projection,
    build_session_rounds,
    build_session_timeline_rounds,
    find_round_by_run_id,
    paginate_rounds,
    timeline_rounds,
)

LOGGER = get_logger(__name__)
SESSION_RECOVERY_CACHE_MS_ENV = "RELAY_TEAMS_SESSION_RECOVERY_CACHE_MS"
SESSION_SNAPSHOT_CACHE_MS_ENV = "RELAY_TEAMS_SESSION_SNAPSHOT_CACHE_MS"
SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS_ENV = (
    "RELAY_TEAMS_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS"
)
DEFAULT_SESSION_RECOVERY_CACHE_MS = 1000
DEFAULT_SESSION_SNAPSHOT_REFRESH_MIN_INTERVAL_MS = 500
_SNAPSHOT_KIND_RECOVERY = "recovery"
_SNAPSHOT_KIND_ROUNDS = "rounds"
_SNAPSHOT_KIND_SUBAGENTS = "subagents"
_SNAPSHOT_KIND_AGENTS = "agents"
_SNAPSHOT_KIND_TASKS = "tasks"
_SNAPSHOT_KIND_TOKEN_USAGE = "token_usage"
_SNAPSHOT_REFRESH_EVENT_TYPES = frozenset(
    {
        RunEventType.RUN_STARTED,
        RunEventType.RUN_PAUSED,
        RunEventType.RUN_RESUMED,
        RunEventType.RUN_COMPLETED,
        RunEventType.RUN_FAILED,
        RunEventType.RUN_STOPPED,
        RunEventType.TOOL_RESULT,
        RunEventType.TOOL_APPROVAL_REQUESTED,
        RunEventType.TOOL_APPROVAL_RESOLVED,
        RunEventType.TODO_UPDATED,
        RunEventType.USER_QUESTION_REQUESTED,
        RunEventType.USER_QUESTION_ANSWERED,
        RunEventType.BACKGROUND_TASK_STARTED,
        RunEventType.BACKGROUND_TASK_UPDATED,
        RunEventType.BACKGROUND_TASK_COMPLETED,
        RunEventType.BACKGROUND_TASK_STOPPED,
    }
)
_TERMINAL_RUN_EVENT_TYPES = frozenset(
    {
        RunEventType.RUN_COMPLETED,
        RunEventType.RUN_FAILED,
        RunEventType.RUN_STOPPED,
    }
)
_LIST_CACHE_DIRTY_EVENT_TYPES = frozenset(
    {
        RunEventType.RUN_STARTED,
        RunEventType.RUN_PAUSED,
        RunEventType.RUN_RESUMED,
        RunEventType.RUN_COMPLETED,
        RunEventType.RUN_FAILED,
        RunEventType.RUN_STOPPED,
        RunEventType.TOOL_APPROVAL_REQUESTED,
        RunEventType.TOOL_APPROVAL_RESOLVED,
        RunEventType.USER_QUESTION_REQUESTED,
        RunEventType.USER_QUESTION_ANSWERED,
        RunEventType.BACKGROUND_TASK_STARTED,
        RunEventType.BACKGROUND_TASK_COMPLETED,
        RunEventType.BACKGROUND_TASK_STOPPED,
    }
)


def _event_is_spawn_subagent_tool_event(event: RunEvent) -> bool:
    if event.event_type not in {RunEventType.TOOL_CALL, RunEventType.TOOL_RESULT}:
        return False
    try:
        payload = json.loads(event.payload_json or "{}")
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    tool_name = payload.get("tool_name")
    return isinstance(tool_name, str) and tool_name.strip() == "spawn_subagent"


class _RecoverySnapshotCacheEntry:
    def __init__(
        self, *, snapshot: dict[str, object], updated_monotonic: float
    ) -> None:
        self.snapshot = snapshot
        self.updated_monotonic = updated_monotonic
        self.dirty = False
        self.requires_fresh_read = False
        self.refresh_started_monotonic = 0.0


def _resolve_positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session.service.invalid_env",
            message="Ignoring invalid session service environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    if value < 1:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session.service.invalid_env",
            message="Ignoring non-positive session service environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    return value


class SessionReadModelMixin:  # pragma: no cover
    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    @staticmethod
    def _subagents_snapshot_key(session_id: str) -> str:
        return f"{_SNAPSHOT_KIND_SUBAGENTS}|{session_id}"

    @staticmethod
    def _agents_snapshot_key(session_id: str) -> str:
        return f"{_SNAPSHOT_KIND_AGENTS}|{session_id}"

    @staticmethod
    def _tasks_snapshot_key(session_id: str) -> str:
        return f"{_SNAPSHOT_KIND_TASKS}|{session_id}"

    @staticmethod
    def _token_usage_snapshot_key(session_id: str) -> str:
        return f"{_SNAPSHOT_KIND_TOKEN_USAGE}|{session_id}"

    @staticmethod
    def _resolve_session_snapshot_cache_ms() -> int:
        raw_value = os.environ.get(SESSION_SNAPSHOT_CACHE_MS_ENV)
        if raw_value is not None and raw_value.strip():
            return _resolve_positive_int_env(
                SESSION_SNAPSHOT_CACHE_MS_ENV,
                DEFAULT_SESSION_RECOVERY_CACHE_MS,
            )
        return _resolve_positive_int_env(
            SESSION_RECOVERY_CACHE_MS_ENV,
            DEFAULT_SESSION_RECOVERY_CACHE_MS,
        )

    def _observe_run_event_for_snapshot_dirty(self, event: RunEvent) -> None:
        session_id = event.session_id.strip()
        if not session_id:
            return
        subagent_count_dirty = _event_is_spawn_subagent_tool_event(event)
        list_cache_dirty = (
            event.event_type in _LIST_CACHE_DIRTY_EVENT_TYPES or subagent_count_dirty
        )
        if list_cache_dirty:
            self._invalidate_list_sessions_cache()
            if event.event_type in _TERMINAL_RUN_EVENT_TYPES:
                self._merge_terminal_session_projection_into_list_cache(session_id)
        requires_fresh_read = event.event_type in {
            *_TERMINAL_RUN_EVENT_TYPES,
            RunEventType.USER_QUESTION_REQUESTED,
            RunEventType.USER_QUESTION_ANSWERED,
            RunEventType.TOOL_APPROVAL_REQUESTED,
            RunEventType.TOOL_APPROVAL_RESOLVED,
        }
        self._mark_session_snapshot_cache_dirty(
            session_id,
            requires_fresh_read=requires_fresh_read,
        )
        if (
            event.event_type not in _SNAPSHOT_REFRESH_EVENT_TYPES
            and not subagent_count_dirty
        ):
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        if list_cache_dirty:
            self._ensure_list_sessions_refresh_task(force=True)
        self._schedule_dirty_session_snapshot_refresh(session_id)

    def _merge_terminal_session_projection_into_list_cache(
        self, session_id: str
    ) -> None:
        try:
            record = self._with_terminal_run_projection(
                self._session_repo.get(session_id)
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="session.list_cache.terminal_projection_merge_skipped",
                message="Skipped terminal projection merge for session list cache",
                payload={
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                },
            )
            return
        self._merge_record_into_list_sessions_cache(record)

    def _mark_session_snapshot_cache_dirty(
        self, session_id: str, *, requires_fresh_read: bool = False
    ) -> None:
        with self._recovery_snapshot_cache_lock:
            entry = self._recovery_snapshot_cache.get(session_id)
            if entry is not None:
                entry.dirty = True
                entry.requires_fresh_read = (
                    entry.requires_fresh_read or requires_fresh_read
                )
        with self._subagents_snapshot_cache_lock:
            entry = self._subagents_snapshot_cache.get(
                self._subagents_snapshot_key(session_id)
            )
            if entry is not None:
                entry.dirty = True
                entry.requires_fresh_read = (
                    entry.requires_fresh_read or requires_fresh_read
                )
        with self._agents_snapshot_cache_lock:
            entry = self._agents_snapshot_cache.get(
                self._agents_snapshot_key(session_id)
            )
            if entry is not None:
                entry.dirty = True
                entry.requires_fresh_read = (
                    entry.requires_fresh_read or requires_fresh_read
                )
        with self._tasks_snapshot_cache_lock:
            entry = self._tasks_snapshot_cache.get(self._tasks_snapshot_key(session_id))
            if entry is not None:
                entry.dirty = True
                entry.requires_fresh_read = (
                    entry.requires_fresh_read or requires_fresh_read
                )
        with self._token_usage_snapshot_cache_lock:
            entry = self._token_usage_snapshot_cache.get(
                self._token_usage_snapshot_key(session_id)
            )
            if entry is not None:
                entry.dirty = True
                entry.requires_fresh_read = (
                    entry.requires_fresh_read or requires_fresh_read
                )
        with self._rounds_snapshot_cache_lock:
            for key, entry in self._rounds_snapshot_cache.items():
                if key.startswith(f"{session_id}|"):
                    entry.dirty = True
                    entry.requires_fresh_read = (
                        entry.requires_fresh_read or requires_fresh_read
                    )

    def _clear_session_snapshot_caches(self, session_id: str) -> None:
        with self._recovery_snapshot_cache_lock:
            self._recovery_snapshot_cache.pop(session_id, None)
        exact_cache_keys = (
            (
                self._subagents_snapshot_cache,
                self._subagents_snapshot_cache_lock,
                self._subagents_snapshot_key(session_id),
            ),
            (
                self._agents_snapshot_cache,
                self._agents_snapshot_cache_lock,
                self._agents_snapshot_key(session_id),
            ),
            (
                self._tasks_snapshot_cache,
                self._tasks_snapshot_cache_lock,
                self._tasks_snapshot_key(session_id),
            ),
            (
                self._token_usage_snapshot_cache,
                self._token_usage_snapshot_cache_lock,
                self._token_usage_snapshot_key(session_id),
            ),
        )
        for cache, cache_lock, cache_key in exact_cache_keys:
            with cache_lock:
                cache.pop(cache_key, None)
        with self._rounds_snapshot_cache_lock:
            for cache_key in tuple(self._rounds_snapshot_cache):
                if cache_key.startswith(f"{session_id}|"):
                    self._rounds_snapshot_cache.pop(cache_key, None)
                    self._rounds_snapshot_args_by_key.pop(cache_key, None)

        task_keys = (
            (self._recovery_refresh_tasks, (session_id,)),
            (
                self._subagents_refresh_tasks,
                (self._subagents_snapshot_key(session_id),),
            ),
            (self._agents_refresh_tasks, (self._agents_snapshot_key(session_id),)),
            (self._tasks_refresh_tasks, (self._tasks_snapshot_key(session_id),)),
            (
                self._token_usage_refresh_tasks,
                (self._token_usage_snapshot_key(session_id),),
            ),
            (
                self._rounds_refresh_tasks,
                tuple(
                    key
                    for key in self._rounds_refresh_tasks
                    if key.startswith(f"{session_id}|")
                ),
            ),
        )
        for tasks, keys in task_keys:
            for key in keys:
                task = tasks.pop(key, None)
                if task is not None:
                    self._cancel_snapshot_refresh_task(task)

    @staticmethod
    def _cancel_snapshot_refresh_task(task: asyncio.Task[None]) -> None:
        if task.done():
            return
        loop = task.get_loop()
        if loop.is_closed():
            task.cancel()
            return
        loop.call_soon_threadsafe(task.cancel)

    def _schedule_dirty_session_snapshot_refresh(self, session_id: str) -> None:
        with self._recovery_snapshot_cache_lock:
            has_recovery_snapshot = session_id in self._recovery_snapshot_cache
        if has_recovery_snapshot:
            self._ensure_recovery_refresh_task(session_id)
        subagents_key = self._subagents_snapshot_key(session_id)
        with self._subagents_snapshot_cache_lock:
            has_subagents_snapshot = subagents_key in self._subagents_snapshot_cache
        if has_subagents_snapshot:
            self._ensure_subagents_refresh_task(session_id)
        agents_key = self._agents_snapshot_key(session_id)
        with self._agents_snapshot_cache_lock:
            has_agents_snapshot = agents_key in self._agents_snapshot_cache
        if has_agents_snapshot:
            self._ensure_agents_refresh_task(session_id)
        tasks_key = self._tasks_snapshot_key(session_id)
        with self._tasks_snapshot_cache_lock:
            has_tasks_snapshot = tasks_key in self._tasks_snapshot_cache
        if has_tasks_snapshot:
            self._ensure_tasks_refresh_task(session_id)
        token_usage_key = self._token_usage_snapshot_key(session_id)
        with self._token_usage_snapshot_cache_lock:
            has_token_usage_snapshot = (
                token_usage_key in self._token_usage_snapshot_cache
            )
        if has_token_usage_snapshot:
            self._ensure_token_usage_refresh_task(session_id)
        with self._rounds_snapshot_cache_lock:
            rounds_cache_keys = tuple(self._rounds_snapshot_cache.keys())
        for key in rounds_cache_keys:
            if key.startswith(f"{session_id}|"):
                self._ensure_rounds_refresh_task(key)

    def list_normal_mode_subagents(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        session = self._session_repo.get(session_id)
        if session.session_mode != SessionMode.NORMAL:
            return ()
        root_tasks_by_run: dict[str, object] = {}
        for task in self._task_repo.list_by_session(session_id):
            if task.envelope.parent_task_id is None:
                root_tasks_by_run[task.envelope.trace_id] = task
        records = [
            record
            for record in self._agent_repo.list_by_session(session_id)
            if self._is_normal_mode_subagent_record(record, session=session)
        ]
        records.sort(key=lambda item: (item.updated_at, item.created_at), reverse=True)
        run_ids = tuple(dict.fromkeys(record.run_id for record in records))
        runtime_by_run = {
            runtime.run_id: runtime
            for runtime in self._run_runtime_repo.list_by_session(session_id)
            if runtime.run_id in run_ids
        }
        run_state_by_run = (
            {
                run_state.run_id: run_state
                for run_state in self._run_state_repo.list_by_session(session_id)
                if run_state.run_id in run_ids
            }
            if self._run_state_repo is not None
            else {}
        )
        approval_counts = (
            self._approval_ticket_repo.count_open_by_run_ids(run_ids)
            if self._approval_ticket_repo is not None
            else {}
        )
        question_counts = (
            self._user_question_repo.count_open_by_run_ids(run_ids)
            if self._user_question_repo is not None
            else {}
        )
        return tuple(
            {
                **self._normal_mode_subagent_projection(
                    record,
                    runtime_by_run=runtime_by_run,
                    run_state_by_run=run_state_by_run,
                    approval_counts=approval_counts,
                    question_counts=question_counts,
                ),
                "title": self._subagent_title_for_run(
                    run_id=record.run_id,
                    root_tasks_by_run=root_tasks_by_run,
                ),
            }
            for record in records
        )

    async def list_normal_mode_subagents_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self.list_normal_mode_subagents, session_id)

    async def get_cached_normal_mode_subagents_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        key = self._subagents_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._subagents_snapshot_cache,
            cache_lock=self._subagents_snapshot_cache_lock,
            key=key,
        )
        now = monotonic()
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_subagents_refresh_task(session_id)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        session = await asyncio.to_thread(self._session_repo.get, session_id)
        if session.session_mode == SessionMode.NORMAL:
            try:
                fresh_snapshot = await asyncio.wait_for(
                    self._build_subagents_snapshot_async(session_id),
                    timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
                )
                fresh_entry = _RecoverySnapshotCacheEntry(
                    snapshot=fresh_snapshot,
                    updated_monotonic=monotonic(),
                )
                with self._subagents_snapshot_cache_lock:
                    self._subagents_snapshot_cache[key] = fresh_entry
                return self._snapshot_response(
                    fresh_entry,
                    stale=False,
                    now=monotonic(),
                    cache_hit=False,
                )
            except asyncio.TimeoutError:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="session.subagents_cache.cold_miss_timeout",
                    message=(
                        "Session subagents cold cache build exceeded fast-read budget"
                    ),
                    payload={
                        "session_id": session_id,
                        "timeout_ms": int(
                            LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000
                        ),
                    },
                )
        snapshot = {
            "items": [],
            "snapshot_refresh_ms": 0,
            "snapshot_cache_hit": False,
        }
        entry = _RecoverySnapshotCacheEntry(
            snapshot=snapshot,
            updated_monotonic=monotonic(),
        )
        entry.dirty = session.session_mode == SessionMode.NORMAL
        with self._subagents_snapshot_cache_lock:
            self._subagents_snapshot_cache[key] = entry
        if entry.dirty:
            self._ensure_subagents_refresh_task(session_id)
        return self._snapshot_response(
            entry,
            stale=entry.dirty,
            now=monotonic(),
            cache_hit=False,
        )

    def get_fast_cached_normal_mode_subagents_snapshot(
        self, session_id: str
    ) -> dict[str, object] | None:
        key = self._subagents_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._subagents_snapshot_cache,
            cache_lock=self._subagents_snapshot_cache_lock,
            key=key,
        )
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale and cached.dirty:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_subagents_refresh_task(session_id)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    async def list_cached_normal_mode_subagents_async(
        self,
        session_id: str,
    ) -> tuple[dict[str, object], ...]:
        snapshot = await self.get_cached_normal_mode_subagents_snapshot_async(
            session_id
        )
        items = snapshot.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(item for item in items if isinstance(item, dict))

    async def _build_subagents_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        started = monotonic()
        items = await asyncio.to_thread(self.list_normal_mode_subagents, session_id)
        return {
            "items": [dict(item) for item in items],
            "snapshot_refresh_ms": int((monotonic() - started) * 1000),
            "snapshot_cache_hit": False,
        }

    def _ensure_subagents_refresh_task(self, session_id: str) -> None:
        key = self._subagents_snapshot_key(session_id)
        if not self._should_start_snapshot_refresh(
            tasks=self._subagents_refresh_tasks,
            cache=self._subagents_snapshot_cache,
            cache_lock=self._subagents_snapshot_cache_lock,
            key=key,
        ):
            return
        task = asyncio.create_task(self._refresh_subagents_snapshot_cache(session_id))
        self._subagents_refresh_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_SUBAGENTS,
                key=key,
                task=completed,
            )
        )

    async def _refresh_subagents_snapshot_cache(self, session_id: str) -> None:
        snapshot = await self._build_subagents_snapshot_async(session_id)
        with self._subagents_snapshot_cache_lock:
            entry = _RecoverySnapshotCacheEntry(
                snapshot=snapshot,
                updated_monotonic=monotonic(),
            )
            self._subagents_snapshot_cache[self._subagents_snapshot_key(session_id)] = (
                entry
            )

    async def stream_normal_mode_subagent_events(
        self,
        session_id: str,
        *,
        after_event_id: int = 0,
    ) -> AsyncIterator[RunEvent]:
        session = self._session_repo.get(session_id)
        if session.session_mode != SessionMode.NORMAL:
            return

        queue = (
            self._run_event_hub.subscribe_session(session_id)
            if self._run_event_hub is not None
            else None
        )
        replay_high_watermark = max(0, int(after_event_id))
        subagent_run_ids = self._subagent_run_ids(session_id)
        try:
            if self._event_log is not None:
                known_rows = (
                    await self._event_log.list_by_session_run_ids_after_id_async(
                        session_id,
                        tuple(sorted(subagent_run_ids)),
                        replay_high_watermark,
                    )
                    if subagent_run_ids
                    else ()
                )
                legacy_rows = await self._event_log.list_subagent_run_events_by_session_after_id_async(
                    session_id,
                    replay_high_watermark,
                )
                rows_by_id = {
                    int(row["id"]): row
                    for row in (*known_rows, *legacy_rows)
                    if isinstance(row.get("id"), int)
                }
                rows = tuple(rows_by_id[key] for key in sorted(rows_by_id))
                for row in rows:
                    event = self._run_event_from_log_row(row)
                    if event is None:
                        continue
                    if event.event_id is not None:
                        replay_high_watermark = max(
                            replay_high_watermark,
                            event.event_id,
                        )
                    yield event

            if queue is None:
                return

            while True:
                event = await queue.get()
                if event.session_id != session_id:
                    continue
                if event.run_id not in subagent_run_ids and (
                    event.event_type in _SNAPSHOT_REFRESH_EVENT_TYPES
                    or self._is_legacy_subagent_run_id(event.run_id)
                ):
                    subagent_run_ids = self._subagent_run_ids(session_id)
                if (
                    event.run_id not in subagent_run_ids
                    and not self._is_legacy_subagent_run_id(event.run_id)
                ):
                    continue
                event_id = event.event_id
                if event_id is not None and event_id <= replay_high_watermark:
                    continue
                if event_id is not None:
                    replay_high_watermark = max(
                        replay_high_watermark,
                        event_id,
                    )
                yield event
        finally:
            if queue is not None and self._run_event_hub is not None:
                self._run_event_hub.unsubscribe_session(session_id, queue)

    def list_agents_in_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        session = self._session_repo.get(session_id)
        latest_by_role: dict[str, AgentRuntimeRecord] = {}
        for record in self._agent_repo.list_by_session(session_id):
            if self._is_normal_mode_subagent_record(record, session=session):
                continue
            existing = latest_by_role.get(record.role_id)
            if existing is None or (
                record.updated_at,
                record.created_at,
            ) >= (
                existing.updated_at,
                existing.created_at,
            ):
                latest_by_role[record.role_id] = record
        return tuple(
            self._agent_projection(latest_by_role[role_id])
            for role_id in sorted(latest_by_role.keys())
        )

    async def list_agents_in_session_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        return await asyncio.to_thread(self.list_agents_in_session, session_id)

    async def get_cached_agents_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        key = self._agents_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._agents_snapshot_cache,
            cache_lock=self._agents_snapshot_cache_lock,
            key=key,
        )
        now = monotonic()
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_agents_refresh_task(session_id)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        _ = await asyncio.to_thread(self._session_repo.get, session_id)
        try:
            fresh_snapshot = await asyncio.wait_for(
                self._build_agents_snapshot_async(session_id),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
            fresh_entry = _RecoverySnapshotCacheEntry(
                snapshot=fresh_snapshot,
                updated_monotonic=monotonic(),
            )
            with self._agents_snapshot_cache_lock:
                self._agents_snapshot_cache[key] = fresh_entry
            return self._snapshot_response(
                fresh_entry,
                stale=False,
                now=monotonic(),
                cache_hit=False,
            )
        except asyncio.TimeoutError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.agents_cache.cold_miss_timeout",
                message="Session agents cold cache build exceeded fast-read budget",
                payload={
                    "session_id": session_id,
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
        entry = _RecoverySnapshotCacheEntry(
            snapshot={
                "items": [],
                "snapshot_refresh_ms": 0,
                "snapshot_cache_hit": False,
            },
            updated_monotonic=monotonic(),
        )
        entry.dirty = True
        with self._agents_snapshot_cache_lock:
            self._agents_snapshot_cache[key] = entry
        self._ensure_agents_refresh_task(session_id)
        return self._snapshot_response(
            entry, stale=True, now=monotonic(), cache_hit=False
        )

    def get_fast_cached_agents_snapshot(
        self, session_id: str
    ) -> dict[str, object] | None:
        key = self._agents_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._agents_snapshot_cache,
            cache_lock=self._agents_snapshot_cache_lock,
            key=key,
        )
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale and cached.dirty:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_agents_refresh_task(session_id)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    async def list_cached_agents_in_session_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        snapshot = await self.get_cached_agents_snapshot_async(session_id)
        items = snapshot.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(item for item in items if isinstance(item, dict))

    async def _build_agents_snapshot_async(self, session_id: str) -> dict[str, object]:
        started = monotonic()
        items = await asyncio.to_thread(self.list_agents_in_session, session_id)
        return {
            "items": [dict(item) for item in items],
            "snapshot_refresh_ms": int((monotonic() - started) * 1000),
            "snapshot_cache_hit": False,
        }

    def _ensure_agents_refresh_task(self, session_id: str) -> None:
        key = self._agents_snapshot_key(session_id)
        if not self._should_start_snapshot_refresh(
            tasks=self._agents_refresh_tasks,
            cache=self._agents_snapshot_cache,
            cache_lock=self._agents_snapshot_cache_lock,
            key=key,
        ):
            return
        task = asyncio.create_task(self._refresh_agents_snapshot_cache(session_id))
        self._agents_refresh_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_AGENTS,
                key=key,
                task=completed,
            )
        )

    async def _refresh_agents_snapshot_cache(self, session_id: str) -> None:
        snapshot = await self._build_agents_snapshot_async(session_id)
        with self._agents_snapshot_cache_lock:
            self._agents_snapshot_cache[self._agents_snapshot_key(session_id)] = (
                _RecoverySnapshotCacheEntry(
                    snapshot=snapshot,
                    updated_monotonic=monotonic(),
                )
            )

    def get_session_tasks(self, session_id: str) -> list[dict[str, object]]:
        records = self._task_repo.list_by_session(session_id)
        return [
            {
                "task_id": record.envelope.task_id,
                "title": record.envelope.title or record.envelope.objective[:80],
                "assigned_role_id": record.envelope.role_id,
                "status": record.status.value,
                "assigned_instance_id": record.assigned_instance_id,
                "role_id": record.envelope.role_id,
                "instance_id": record.assigned_instance_id,
                "run_id": record.envelope.trace_id,
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
                "spec_artifact_id": record.envelope.spec_artifact_id,
                "spec_source_task_id": record.envelope.spec_source_task_id,
                "spec_summary": (
                    record.envelope.spec.summary if record.envelope.spec else ""
                ),
                "spec_strictness": (
                    record.envelope.spec.strictness.value
                    if record.envelope.spec
                    else ""
                ),
                "evidence_bundle": (
                    record.envelope.evidence_bundle.model_dump(mode="json")
                    if record.envelope.evidence_bundle
                    else None
                ),
            }
            for record in records
            if record.envelope.parent_task_id is not None
        ]

    async def get_session_tasks_async(self, session_id: str) -> list[dict[str, object]]:
        return await asyncio.to_thread(self.get_session_tasks, session_id)

    async def get_cached_session_tasks_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        key = self._tasks_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._tasks_snapshot_cache,
            cache_lock=self._tasks_snapshot_cache_lock,
            key=key,
        )
        now = monotonic()
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_tasks_refresh_task(session_id)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        _ = await asyncio.to_thread(self._session_repo.get, session_id)
        try:
            fresh_snapshot = await asyncio.wait_for(
                self._build_tasks_snapshot_async(session_id),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
            fresh_entry = _RecoverySnapshotCacheEntry(
                snapshot=fresh_snapshot,
                updated_monotonic=monotonic(),
            )
            with self._tasks_snapshot_cache_lock:
                self._tasks_snapshot_cache[key] = fresh_entry
            return self._snapshot_response(
                fresh_entry,
                stale=False,
                now=monotonic(),
                cache_hit=False,
            )
        except asyncio.TimeoutError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.tasks_cache.cold_miss_timeout",
                message="Session tasks cold cache build exceeded fast-read budget",
                payload={
                    "session_id": session_id,
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
        entry = _RecoverySnapshotCacheEntry(
            snapshot={
                "items": [],
                "snapshot_refresh_ms": 0,
                "snapshot_cache_hit": False,
            },
            updated_monotonic=monotonic(),
        )
        entry.dirty = True
        with self._tasks_snapshot_cache_lock:
            self._tasks_snapshot_cache[key] = entry
        self._ensure_tasks_refresh_task(session_id)
        return self._snapshot_response(
            entry, stale=True, now=monotonic(), cache_hit=False
        )

    def get_fast_cached_session_tasks_snapshot(
        self, session_id: str
    ) -> dict[str, object] | None:
        key = self._tasks_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._tasks_snapshot_cache,
            cache_lock=self._tasks_snapshot_cache_lock,
            key=key,
        )
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale and cached.dirty:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_tasks_refresh_task(session_id)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    async def list_cached_session_tasks_async(
        self, session_id: str
    ) -> tuple[dict[str, object], ...]:
        snapshot = await self.get_cached_session_tasks_snapshot_async(session_id)
        items = snapshot.get("items")
        if not isinstance(items, list):
            return ()
        return tuple(item for item in items if isinstance(item, dict))

    async def _build_tasks_snapshot_async(self, session_id: str) -> dict[str, object]:
        started = monotonic()
        items = await asyncio.to_thread(self.get_session_tasks, session_id)
        return {
            "items": [dict(item) for item in items],
            "snapshot_refresh_ms": int((monotonic() - started) * 1000),
            "snapshot_cache_hit": False,
        }

    def _ensure_tasks_refresh_task(self, session_id: str) -> None:
        key = self._tasks_snapshot_key(session_id)
        if not self._should_start_snapshot_refresh(
            tasks=self._tasks_refresh_tasks,
            cache=self._tasks_snapshot_cache,
            cache_lock=self._tasks_snapshot_cache_lock,
            key=key,
        ):
            return
        task = asyncio.create_task(self._refresh_tasks_snapshot_cache(session_id))
        self._tasks_refresh_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_TASKS,
                key=key,
                task=completed,
            )
        )

    async def _refresh_tasks_snapshot_cache(self, session_id: str) -> None:
        snapshot = await self._build_tasks_snapshot_async(session_id)
        with self._tasks_snapshot_cache_lock:
            self._tasks_snapshot_cache[self._tasks_snapshot_key(session_id)] = (
                _RecoverySnapshotCacheEntry(
                    snapshot=snapshot,
                    updated_monotonic=monotonic(),
                )
            )

    def build_session_rounds(
        self,
        session_id: str,
        *,
        included_run_ids: set[str] | None = None,
        include_history_markers: bool = True,
    ) -> list[dict[str, object]]:
        excluded_run_ids = self._subagent_run_ids(session_id)
        selected_run_ids = (
            tuple(sorted(included_run_ids)) if included_run_ids is not None else None
        )
        todos_by_run_id = (
            {
                snapshot.run_id: snapshot.model_dump(mode="json")
                for snapshot in self._todo_service.list_for_session(session_id)
                if included_run_ids is None or snapshot.run_id in included_run_ids
            }
            if self._todo_service is not None
            else {}
        )
        intent_input_parts_by_run = (
            self._session_run_intent_input_parts_by_run(session_id)
            if included_run_ids is None
            else {
                run_id: parts
                for run_id, parts in self._session_run_intent_input_parts_by_run(
                    session_id
                ).items()
                if run_id in included_run_ids
            }
        )
        runtime_by_run = {
            run_id: runtime
            for run_id, runtime in self._session_run_runtime_by_run(session_id).items()
            if included_run_ids is None or run_id in included_run_ids
        }
        rounds = build_session_rounds(
            session_id=session_id,
            agent_repo=self._agent_repo,
            task_repo=self._task_repo,
            approval_tickets_by_run=approvals_to_projection(
                [
                    record
                    for record in self._approval_ticket_repo.list_open_by_session(
                        session_id
                    )
                    if included_run_ids is None or record.run_id in included_run_ids
                ]
            ),
            run_runtime_repo=self._run_runtime_repo,
            get_session_messages=lambda current_session_id: cast(
                list[dict[str, object]],
                (
                    self._message_repo.get_messages_by_session(
                        current_session_id,
                        include_cleared=True,
                        include_hidden_from_context=True,
                    )
                    if selected_run_ids is None
                    else self._message_repo.get_messages_by_session_run_ids(
                        current_session_id,
                        selected_run_ids,
                        include_cleared=True,
                        include_hidden_from_context=True,
                    )
                ),
            ),
            get_run_intent_input=intent_input_parts_by_run.get,
            get_session_history_markers=(
                self._get_session_history_markers if include_history_markers else None
            ),
            get_session_events=(
                self._get_round_projection_events
                if selected_run_ids is None
                else lambda current_session_id: (
                    self._get_round_projection_events_for_runs(
                        current_session_id,
                        selected_run_ids,
                    )
                )
            ),
            excluded_run_ids=excluded_run_ids,
            included_run_ids=included_run_ids,
            run_runtime_by_run=runtime_by_run,
        )
        question_counts_by_run = self._pending_user_question_counts_by_run(session_id)
        for round_item in rounds:
            runtime = runtime_by_run.get(str(round_item.get("run_id") or ""))
            pending = round_item.get("pending_tool_approvals")
            approval_count = len(pending) if isinstance(pending, list) else 0
            if runtime is None:
                continue
            question_count = question_counts_by_run.get(runtime.run_id, 0)
            round_item["run_status"] = runtime.status.value
            round_item["run_phase"] = self._public_phase(
                runtime,
                approval_count,
                question_count,
            )
            round_item["is_recoverable"] = self._is_runtime_publicly_recoverable(
                runtime
            )
            todo = todos_by_run_id.get(str(round_item.get("run_id") or ""))
            if todo is not None:
                round_item["todo"] = todo
        return rounds

    def build_session_timeline_rounds(self, session_id: str) -> list[dict[str, object]]:
        excluded_run_ids = self._subagent_run_ids(session_id)
        todos_by_run_id = (
            {
                snapshot.run_id: snapshot.model_dump(mode="json")
                for snapshot in self._todo_service.list_for_session(session_id)
            }
            if self._todo_service is not None
            else {}
        )
        intent_input_parts_by_run = self._session_run_intent_input_parts_by_run(
            session_id
        )
        runtime_by_run = self._session_run_runtime_by_run(session_id)
        rounds = build_session_timeline_rounds(
            session_id=session_id,
            task_repo=self._task_repo,
            approval_tickets_by_run=approvals_to_projection(
                self._approval_ticket_repo.list_open_by_session(session_id)
            ),
            run_runtime_repo=self._run_runtime_repo,
            get_session_user_messages=lambda current_session_id: cast(
                list[dict[str, object]],
                self._message_repo.get_user_messages_by_session(
                    current_session_id,
                    include_cleared=True,
                    include_hidden_from_context=True,
                ),
            ),
            get_run_intent_input=intent_input_parts_by_run.get,
            get_session_history_markers=self._get_session_history_markers,
            get_session_events=self._get_round_projection_events,
            excluded_run_ids=excluded_run_ids,
            run_runtime_by_run=runtime_by_run,
        )
        question_counts_by_run = self._pending_user_question_counts_by_run(session_id)
        for round_item in rounds:
            runtime = runtime_by_run.get(str(round_item.get("run_id") or ""))
            raw_approval_count = round_item.get("pending_tool_approval_count")
            approval_count = (
                raw_approval_count
                if isinstance(raw_approval_count, int)
                and not isinstance(raw_approval_count, bool)
                else 0
            )
            if runtime is None:
                continue
            question_count = question_counts_by_run.get(runtime.run_id, 0)
            round_item["run_status"] = runtime.status.value
            round_item["run_phase"] = self._public_phase(
                runtime,
                approval_count,
                question_count,
            )
            round_item["is_recoverable"] = self._is_runtime_publicly_recoverable(
                runtime
            )
            todo = todos_by_run_id.get(str(round_item.get("run_id") or ""))
            if todo is not None:
                round_item["todo"] = todo
        return rounds

    def _session_run_runtime_by_run(
        self,
        session_id: str,
    ) -> dict[str, RunRuntimeRecord]:
        return {
            runtime.run_id: runtime
            for runtime in self._run_runtime_repo.list_by_session(session_id)
        }

    def _get_run_intent_input_parts(
        self, run_id: str
    ) -> tuple[ContentPart, ...] | None:
        if self._run_intent_repo is None:
            return None
        try:
            intent = self._run_intent_repo.get(run_id)
        except KeyError:
            return None
        return intent.display_input or intent.input

    def _session_run_intent_input_parts_by_run(
        self, session_id: str
    ) -> dict[str, tuple[ContentPart, ...]]:
        if self._run_intent_repo is None:
            return {}
        return {
            run_id: intent.display_input or intent.input
            for run_id, intent in self._run_intent_repo.list_by_session(
                session_id
            ).items()
        }

    def get_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
        timeline: bool = False,
        summary: bool = False,
    ) -> dict[str, object]:
        if timeline:
            rounds = self.build_session_timeline_rounds(session_id)
            return timeline_rounds(rounds)
        timeline_items = self.build_session_timeline_rounds(session_id)
        page = paginate_rounds(
            timeline_items,
            limit=limit,
            cursor_run_id=cursor_run_id,
        )
        if summary:
            return page
        page_items = page.get("items")
        if not isinstance(page_items, list) or not page_items:
            return page
        page_run_ids = tuple(
            str(item.get("run_id") or "")
            for item in page_items
            if isinstance(item, dict) and str(item.get("run_id") or "")
        )
        if not page_run_ids:
            return page
        full_rounds = self.build_session_rounds(
            session_id,
            included_run_ids=set(page_run_ids),
            include_history_markers=False,
        )
        full_round_by_run = {
            str(round_item.get("run_id") or ""): round_item
            for round_item in full_rounds
        }
        page_marker_by_run = {
            str(item.get("run_id") or ""): {
                "clear_marker_before": item.get("clear_marker_before"),
                "compaction_marker_before": item.get("compaction_marker_before"),
            }
            for item in page_items
            if isinstance(item, dict) and str(item.get("run_id") or "")
        }
        resolved_items: list[dict[str, object]] = []
        for run_id in page_run_ids:
            full_round = full_round_by_run.get(run_id)
            if full_round is None:
                continue
            markers = page_marker_by_run.get(run_id, {})
            full_round["clear_marker_before"] = markers.get("clear_marker_before")
            full_round["compaction_marker_before"] = markers.get(
                "compaction_marker_before"
            )
            resolved_items.append(full_round)
        page["items"] = resolved_items
        return page

    async def get_session_rounds_async(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
        timeline: bool = False,
        summary: bool = False,
    ) -> dict[str, object]:

        return await asyncio.to_thread(
            self.get_session_rounds,
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
            summary=summary,
        )

    def get_cached_session_rounds(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
        timeline: bool = False,
        summary: bool = False,
    ) -> dict[str, object]:
        return self.get_session_rounds(
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
            summary=summary,
        )

    async def get_cached_session_rounds_async(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
        timeline: bool = False,
        summary: bool = False,
    ) -> dict[str, object]:
        cache_key = (
            f"{session_id}|{limit}|{cursor_run_id or ''}|{int(timeline)}|{int(summary)}"
        )
        self._rounds_snapshot_args_by_key[cache_key] = (
            session_id,
            limit,
            cursor_run_id,
            timeline,
            summary,
        )
        now = monotonic()
        with self._rounds_snapshot_cache_lock:
            cached = self._rounds_snapshot_cache.get(cache_key)
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_rounds_refresh_task(cache_key)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        _ = await asyncio.to_thread(self._session_repo.get, session_id)
        try:
            fresh_snapshot = await asyncio.wait_for(
                self._build_rounds_snapshot_async(
                    session_id,
                    limit=limit,
                    cursor_run_id=cursor_run_id,
                    timeline=timeline,
                    summary=summary,
                ),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
            fresh_entry = _RecoverySnapshotCacheEntry(
                snapshot=fresh_snapshot,
                updated_monotonic=monotonic(),
            )
            with self._rounds_snapshot_cache_lock:
                self._rounds_snapshot_cache[cache_key] = fresh_entry
            return self._snapshot_response(
                fresh_entry,
                stale=False,
                now=monotonic(),
                cache_hit=False,
            )
        except asyncio.TimeoutError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.rounds_cache.cold_miss_timeout",
                message="Session rounds cold cache build exceeded fast-read budget",
                payload={
                    "session_id": session_id,
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
        snapshot = {
            "items": [],
            "has_more": False,
            "next_cursor": None,
            "snapshot_refresh_ms": 0,
            "snapshot_cache_hit": False,
        }
        entry = _RecoverySnapshotCacheEntry(
            snapshot=snapshot,
            updated_monotonic=monotonic(),
        )
        entry.dirty = True
        with self._rounds_snapshot_cache_lock:
            self._rounds_snapshot_cache[cache_key] = entry
        self._ensure_rounds_refresh_task(cache_key)
        return self._snapshot_response(
            entry, stale=True, now=monotonic(), cache_hit=False
        )

    def get_fast_cached_session_rounds_snapshot(
        self,
        session_id: str,
        *,
        limit: int = 8,
        cursor_run_id: str | None = None,
        timeline: bool = False,
        summary: bool = False,
    ) -> dict[str, object] | None:
        cache_key = (
            f"{session_id}|{limit}|{cursor_run_id or ''}|{int(timeline)}|{int(summary)}"
        )
        self._rounds_snapshot_args_by_key[cache_key] = (
            session_id,
            limit,
            cursor_run_id,
            timeline,
            summary,
        )
        with self._rounds_snapshot_cache_lock:
            cached = self._rounds_snapshot_cache.get(cache_key)
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_rounds_refresh_task(cache_key)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    async def _build_rounds_snapshot_async(
        self,
        session_id: str,
        *,
        limit: int,
        cursor_run_id: str | None,
        timeline: bool,
        summary: bool,
    ) -> dict[str, object]:
        started = monotonic()
        snapshot = await asyncio.to_thread(
            self.get_session_rounds,
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
            summary=summary,
        )
        snapshot = dict(snapshot)
        snapshot["snapshot_refresh_ms"] = int((monotonic() - started) * 1000)
        snapshot["snapshot_cache_hit"] = False
        return snapshot

    def _ensure_rounds_refresh_task(self, cache_key: str) -> None:
        if not self._should_start_snapshot_refresh(
            tasks=self._rounds_refresh_tasks,
            cache=self._rounds_snapshot_cache,
            cache_lock=self._rounds_snapshot_cache_lock,
            key=cache_key,
        ):
            return
        args = self._rounds_snapshot_args_by_key.get(cache_key)
        if args is None:
            return
        task = asyncio.create_task(self._refresh_rounds_snapshot_cache(cache_key, args))
        self._rounds_refresh_tasks[cache_key] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_ROUNDS,
                key=cache_key,
                task=completed,
            )
        )

    async def _refresh_rounds_snapshot_cache(
        self,
        cache_key: str,
        args: tuple[str, int, str | None, bool, bool],
    ) -> None:
        session_id, limit, cursor_run_id, timeline, summary = args
        snapshot = await self._build_rounds_snapshot_async(
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
            summary=summary,
        )
        with self._rounds_snapshot_cache_lock:
            self._rounds_snapshot_cache[cache_key] = _RecoverySnapshotCacheEntry(
                snapshot=snapshot,
                updated_monotonic=monotonic(),
            )

    def get_round(self, session_id: str, run_id: str) -> dict[str, object]:
        safe_run_id = str(run_id or "").strip()
        timeline_item = next(
            (
                item
                for item in self.build_session_timeline_rounds(session_id)
                if str(item.get("run_id") or "") == safe_run_id
            ),
            None,
        )
        rounds = self.build_session_rounds(
            session_id,
            included_run_ids={safe_run_id},
            include_history_markers=False,
        )
        round_item = find_round_by_run_id(rounds, session_id=session_id, run_id=run_id)
        if timeline_item is not None:
            round_item["clear_marker_before"] = timeline_item.get("clear_marker_before")
            round_item["compaction_marker_before"] = timeline_item.get(
                "compaction_marker_before"
            )
        return round_item

    async def get_round_async(self, session_id: str, run_id: str) -> dict[str, object]:

        return await asyncio.to_thread(self.get_round, session_id, run_id)

    def get_recovery_snapshot(self, session_id: str) -> dict[str, object]:
        _ = self._session_repo.get(session_id)
        selected = self._select_active_run(session_id)
        if selected is None:
            return self._empty_recovery_snapshot()

        run_id, runtime = selected
        stream_connected = (
            self._run_event_hub.has_subscribers(run_id)
            if self._run_event_hub is not None
            else False
        )
        approvals = [
            {
                "tool_call_id": record.tool_call_id,
                "tool_name": record.tool_name,
                "args_preview": record.args_preview,
                "role_id": record.role_id,
                "instance_id": record.instance_id,
                "requested_at": record.created_at.isoformat(),
                "status": record.status.value,
                "feedback": record.feedback,
            }
            for record in self._approval_ticket_repo.list_open_by_run(run_id)
        ]
        user_questions = (
            [
                record.model_dump(mode="json")
                for record in self._list_resolvable_user_questions_for_session(
                    session_id
                )
            ]
            if self._user_question_repo is not None
            else []
        )
        run_state = (
            self._run_state_repo.get_run_state(run_id)
            if self._run_state_repo is not None
            else None
        )
        background_tasks = [
            record.model_dump(mode="json", exclude={"output_excerpt"})
            for record in (
                exec_record
                for exec_record in (
                    self._background_task_repository.list_by_run(run_id)
                    if self._background_task_repository is not None
                    else ()
                )
                if exec_record.execution_mode == "background"
            )
        ]
        active_run = {
            "run_id": run_id,
            "status": runtime.status.value,
            "phase": self._public_phase(runtime, len(approvals), len(user_questions)),
            "is_recoverable": self._is_runtime_publicly_recoverable(runtime),
            "last_event_id": (
                int(run_state.last_event_id) if run_state is not None else 0
            ),
            "checkpoint_event_id": (
                int(run_state.checkpoint_event_id) if run_state is not None else 0
            ),
            "pending_tool_approval_count": len(approvals),
            "pending_user_question_count": len(user_questions),
            "background_task_count": len(background_tasks),
            "stream_connected": stream_connected,
            "should_show_recover": self._is_runtime_publicly_recoverable(runtime)
            and not stream_connected,
        }
        paused_subagent = self._paused_subagent_snapshot(runtime)
        try:
            round_snapshot = self.get_round(session_id, run_id)
        except KeyError:
            round_snapshot = None
        if isinstance(round_snapshot, dict):
            active_run["primary_role_id"] = round_snapshot.get("primary_role_id")
            round_snapshot["background_task_count"] = len(background_tasks)
        return {
            "active_run": active_run,
            "background_tasks": background_tasks,
            "pending_tool_approvals": approvals,
            "pending_user_questions": user_questions,
            "paused_subagent": paused_subagent,
            "round_snapshot": round_snapshot,
        }

    async def get_recovery_snapshot_async(self, session_id: str) -> dict[str, object]:

        return await asyncio.to_thread(self.get_recovery_snapshot, session_id)

    async def get_cached_recovery_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        cached = self._recovery_cache_entry(session_id)
        now = monotonic()
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_recovery_refresh_task(session_id)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        _ = await asyncio.to_thread(self._session_repo.get, session_id)
        try:
            fresh_snapshot = await asyncio.wait_for(
                self.get_recovery_snapshot_async(session_id),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
            fresh_entry = _RecoverySnapshotCacheEntry(
                snapshot=fresh_snapshot,
                updated_monotonic=monotonic(),
            )
            with self._recovery_snapshot_cache_lock:
                self._recovery_snapshot_cache[session_id] = fresh_entry
            return self._snapshot_response(
                fresh_entry,
                stale=False,
                now=monotonic(),
                cache_hit=False,
            )
        except asyncio.TimeoutError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.recovery_cache.cold_miss_timeout",
                message="Session recovery cold cache build exceeded fast-read budget",
                payload={
                    "session_id": session_id,
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.recovery_cache.cold_miss_failed",
                message="Session recovery cold cache build failed",
                payload={
                    "session_id": session_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        entry = _RecoverySnapshotCacheEntry(
            snapshot=self._empty_recovery_snapshot(),
            updated_monotonic=monotonic(),
        )
        entry.dirty = True
        with self._recovery_snapshot_cache_lock:
            self._recovery_snapshot_cache[session_id] = entry
        self._ensure_recovery_refresh_task(session_id)
        return self._snapshot_response(
            entry, stale=True, now=monotonic(), cache_hit=False
        )

    def get_fast_cached_recovery_snapshot(
        self,
        session_id: str,
    ) -> dict[str, object] | None:
        cached = self._recovery_cache_entry(session_id)
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_recovery_refresh_task(session_id)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    @staticmethod
    def _empty_recovery_snapshot() -> dict[str, object]:
        return {
            "active_run": None,
            "background_tasks": [],
            "pending_tool_approvals": [],
            "pending_user_questions": [],
            "paused_subagent": None,
            "round_snapshot": None,
        }

    def _seed_empty_recovery_snapshot_cache(self, session_id: str) -> None:
        with self._recovery_snapshot_cache_lock:
            if session_id in self._recovery_snapshot_cache:
                return
            self._recovery_snapshot_cache[session_id] = _RecoverySnapshotCacheEntry(
                snapshot=self._empty_recovery_snapshot(),
                updated_monotonic=monotonic(),
            )

    def _seed_empty_session_snapshot_caches(self, session_id: str) -> None:
        now = monotonic()
        self._seed_empty_recovery_snapshot_cache(session_id)
        self._seed_empty_items_snapshot_cache(
            cache=self._subagents_snapshot_cache,
            cache_lock=self._subagents_snapshot_cache_lock,
            key=self._subagents_snapshot_key(session_id),
            now=now,
        )
        self._seed_empty_items_snapshot_cache(
            cache=self._agents_snapshot_cache,
            cache_lock=self._agents_snapshot_cache_lock,
            key=self._agents_snapshot_key(session_id),
            now=now,
        )
        self._seed_empty_items_snapshot_cache(
            cache=self._tasks_snapshot_cache,
            cache_lock=self._tasks_snapshot_cache_lock,
            key=self._tasks_snapshot_key(session_id),
            now=now,
        )
        with self._token_usage_snapshot_cache_lock:
            token_usage_key = self._token_usage_snapshot_key(session_id)
            if token_usage_key not in self._token_usage_snapshot_cache:
                self._token_usage_snapshot_cache[token_usage_key] = (
                    _RecoverySnapshotCacheEntry(
                        snapshot=self._empty_token_usage_snapshot(session_id),
                        updated_monotonic=now,
                    )
                )
        summary_rounds_key = f"{session_id}|4||0|1"
        default_rounds_key = f"{session_id}|8||0|0"
        self._rounds_snapshot_args_by_key[summary_rounds_key] = (
            session_id,
            4,
            None,
            False,
            True,
        )
        self._rounds_snapshot_args_by_key[default_rounds_key] = (
            session_id,
            8,
            None,
            False,
            False,
        )
        self._seed_empty_items_snapshot_cache(
            cache=self._rounds_snapshot_cache,
            cache_lock=self._rounds_snapshot_cache_lock,
            key=summary_rounds_key,
            now=now,
        )
        self._seed_empty_items_snapshot_cache(
            cache=self._rounds_snapshot_cache,
            cache_lock=self._rounds_snapshot_cache_lock,
            key=default_rounds_key,
            now=now,
        )

    @staticmethod
    def _seed_empty_items_snapshot_cache(
        *,
        cache: dict[str, _RecoverySnapshotCacheEntry],
        cache_lock: Lock,
        key: str,
        now: float,
    ) -> None:
        with cache_lock:
            if key in cache:
                return
            cache[key] = _RecoverySnapshotCacheEntry(
                snapshot={
                    "items": [],
                    "snapshot_refresh_ms": 0,
                    "snapshot_cache_hit": False,
                },
                updated_monotonic=now,
            )

    def _recovery_cache_entry(
        self, session_id: str
    ) -> _RecoverySnapshotCacheEntry | None:
        with self._recovery_snapshot_cache_lock:
            return self._recovery_snapshot_cache.get(session_id)

    @staticmethod
    def _snapshot_response(
        entry: _RecoverySnapshotCacheEntry,
        *,
        stale: bool,
        now: float,
        cache_hit: bool,
    ) -> dict[str, object]:
        snapshot = dict(entry.snapshot)
        snapshot["stale"] = stale
        snapshot["snapshot_age_ms"] = max(
            0,
            int((now - entry.updated_monotonic) * 1000),
        )
        snapshot["snapshot_cache_hit"] = cache_hit
        return snapshot

    def _ensure_recovery_refresh_task(self, session_id: str) -> None:
        if not self._should_start_snapshot_refresh(
            tasks=self._recovery_refresh_tasks,
            cache=self._recovery_snapshot_cache,
            cache_lock=self._recovery_snapshot_cache_lock,
            key=session_id,
        ):
            return
        task = self._recovery_refresh_tasks.get(session_id)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._refresh_recovery_snapshot_cache(session_id))
        self._recovery_refresh_tasks[session_id] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_RECOVERY,
                key=session_id,
                task=completed,
            )
        )

    async def _refresh_recovery_snapshot_cache(self, session_id: str) -> None:
        snapshot = await asyncio.to_thread(self.get_recovery_snapshot, session_id)
        with self._recovery_snapshot_cache_lock:
            self._recovery_snapshot_cache[session_id] = _RecoverySnapshotCacheEntry(
                snapshot=snapshot,
                updated_monotonic=monotonic(),
            )

    @staticmethod
    def _snapshot_cache_entry(
        *,
        cache: dict[str, _RecoverySnapshotCacheEntry],
        cache_lock: Lock,
        key: str,
    ) -> _RecoverySnapshotCacheEntry | None:
        with cache_lock:
            return cache.get(key)

    def _snapshot_entry_is_stale(
        self,
        entry: _RecoverySnapshotCacheEntry,
        *,
        now: float,
    ) -> bool:
        age_ms = int((now - entry.updated_monotonic) * 1000)
        return (
            entry.dirty or entry.requires_fresh_read or age_ms > self._recovery_cache_ms
        )

    def _should_start_snapshot_refresh(
        self,
        *,
        tasks: dict[str, asyncio.Task[None]],
        cache: dict[str, _RecoverySnapshotCacheEntry],
        cache_lock: Lock,
        key: str,
    ) -> bool:
        task = tasks.get(key)
        if task is not None and not task.done():
            return False
        now = monotonic()
        with cache_lock:
            entry = cache.get(key)
            if entry is None:
                return False
            elapsed_ms = int((now - entry.refresh_started_monotonic) * 1000)
            if elapsed_ms < self._snapshot_refresh_min_interval_ms:
                return False
            entry.refresh_started_monotonic = now
        return True

    def _observe_snapshot_refresh_result(
        self,
        *,
        kind: str,
        key: str,
        task: asyncio.Task[None],
    ) -> None:
        if kind == _SNAPSHOT_KIND_ROUNDS:
            self._rounds_refresh_tasks.pop(key, None)
        elif kind == _SNAPSHOT_KIND_SUBAGENTS:
            self._subagents_refresh_tasks.pop(key, None)
        elif kind == _SNAPSHOT_KIND_RECOVERY:
            self._recovery_refresh_tasks.pop(key, None)
        elif kind == _SNAPSHOT_KIND_AGENTS:
            self._agents_refresh_tasks.pop(key, None)
        elif kind == _SNAPSHOT_KIND_TASKS:
            self._tasks_refresh_tasks.pop(key, None)
        elif kind == _SNAPSHOT_KIND_TOKEN_USAGE:
            self._token_usage_refresh_tasks.pop(key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.snapshot_cache.refresh_failed",
                message="Session snapshot cache refresh failed",
                payload={
                    "kind": kind,
                    "key": key,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    def get_token_usage_by_run(self, run_id: str) -> RunTokenUsage:
        return self._token_usage_repo.get_by_run(run_id)

    def get_token_usage_by_session(self, session_id: str) -> SessionTokenUsage:
        return self._token_usage_repo.get_by_session(session_id)

    async def get_cached_token_usage_by_session_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        key = self._token_usage_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._token_usage_snapshot_cache,
            cache_lock=self._token_usage_snapshot_cache_lock,
            key=key,
        )
        now = monotonic()
        if cached is not None:
            stale = self._snapshot_entry_is_stale(cached, now=now)
            if stale:
                self._ensure_token_usage_refresh_task(session_id)
            return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)
        _ = await asyncio.to_thread(self._session_repo.get, session_id)
        try:
            fresh_snapshot = await asyncio.wait_for(
                self._build_token_usage_snapshot_async(session_id),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
            fresh_entry = _RecoverySnapshotCacheEntry(
                snapshot=fresh_snapshot,
                updated_monotonic=monotonic(),
            )
            with self._token_usage_snapshot_cache_lock:
                self._token_usage_snapshot_cache[key] = fresh_entry
            return self._snapshot_response(
                fresh_entry,
                stale=False,
                now=monotonic(),
                cache_hit=False,
            )
        except asyncio.TimeoutError:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.token_usage_cache.cold_miss_timeout",
                message="Session token usage cold cache build exceeded fast-read budget",
                payload={
                    "session_id": session_id,
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
        entry = _RecoverySnapshotCacheEntry(
            snapshot=self._empty_token_usage_snapshot(session_id),
            updated_monotonic=monotonic(),
        )
        entry.dirty = True
        with self._token_usage_snapshot_cache_lock:
            self._token_usage_snapshot_cache[key] = entry
        self._ensure_token_usage_refresh_task(session_id)
        return self._snapshot_response(
            entry, stale=True, now=monotonic(), cache_hit=False
        )

    def get_fast_cached_token_usage_by_session_snapshot(
        self, session_id: str
    ) -> dict[str, object] | None:
        key = self._token_usage_snapshot_key(session_id)
        cached = self._snapshot_cache_entry(
            cache=self._token_usage_snapshot_cache,
            cache_lock=self._token_usage_snapshot_cache_lock,
            key=key,
        )
        if cached is None:
            return None
        now = monotonic()
        stale = self._snapshot_entry_is_stale(cached, now=now)
        if stale and cached.dirty:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._ensure_token_usage_refresh_task(session_id)
        return self._snapshot_response(cached, stale=stale, now=now, cache_hit=True)

    @staticmethod
    def _empty_token_usage_snapshot(session_id: str) -> dict[str, object]:
        return {
            "session_id": session_id,
            "total_input_tokens": 0,
            "total_cached_input_tokens": 0,
            "total_output_tokens": 0,
            "total_reasoning_output_tokens": 0,
            "total_tokens": 0,
            "total_requests": 0,
            "total_tool_calls": 0,
            "by_role": {},
            "snapshot_refresh_ms": 0,
            "snapshot_cache_hit": False,
        }

    @staticmethod
    def _token_usage_snapshot(summary: SessionTokenUsage) -> dict[str, object]:
        return {
            "session_id": summary.session_id,
            "total_input_tokens": summary.total_input_tokens,
            "total_cached_input_tokens": summary.total_cached_input_tokens,
            "total_output_tokens": summary.total_output_tokens,
            "total_reasoning_output_tokens": summary.total_reasoning_output_tokens,
            "total_tokens": summary.total_tokens,
            "total_requests": summary.total_requests,
            "total_tool_calls": summary.total_tool_calls,
            "by_role": {
                role_id: {
                    "role_id": agent.role_id,
                    "input_tokens": agent.input_tokens,
                    "latest_input_tokens": agent.latest_input_tokens,
                    "cached_input_tokens": agent.cached_input_tokens,
                    "max_input_tokens": agent.max_input_tokens,
                    "output_tokens": agent.output_tokens,
                    "reasoning_output_tokens": agent.reasoning_output_tokens,
                    "total_tokens": agent.total_tokens,
                    "requests": agent.requests,
                    "tool_calls": agent.tool_calls,
                    "context_window": agent.context_window,
                    "model_profile": agent.model_profile,
                }
                for role_id, agent in summary.by_role.items()
            },
            "snapshot_cache_hit": False,
        }

    async def _build_token_usage_snapshot_async(
        self, session_id: str
    ) -> dict[str, object]:
        started = monotonic()
        summary = await asyncio.to_thread(self.get_token_usage_by_session, session_id)
        snapshot = self._token_usage_snapshot(summary)
        snapshot["snapshot_refresh_ms"] = int((monotonic() - started) * 1000)
        return snapshot

    def _ensure_token_usage_refresh_task(self, session_id: str) -> None:
        key = self._token_usage_snapshot_key(session_id)
        if not self._should_start_snapshot_refresh(
            tasks=self._token_usage_refresh_tasks,
            cache=self._token_usage_snapshot_cache,
            cache_lock=self._token_usage_snapshot_cache_lock,
            key=key,
        ):
            return
        task = asyncio.create_task(self._refresh_token_usage_snapshot_cache(session_id))
        self._token_usage_refresh_tasks[key] = task
        task.add_done_callback(
            lambda completed: self._observe_snapshot_refresh_result(
                kind=_SNAPSHOT_KIND_TOKEN_USAGE,
                key=key,
                task=completed,
            )
        )

    async def _refresh_token_usage_snapshot_cache(self, session_id: str) -> None:
        snapshot = await self._build_token_usage_snapshot_async(session_id)
        with self._token_usage_snapshot_cache_lock:
            self._token_usage_snapshot_cache[
                self._token_usage_snapshot_key(session_id)
            ] = _RecoverySnapshotCacheEntry(
                snapshot=snapshot,
                updated_monotonic=monotonic(),
            )

    async def get_token_usage_by_run_async(self, run_id: str) -> RunTokenUsage:

        return await asyncio.to_thread(self._token_usage_repo.get_by_run, run_id)

    async def get_token_usage_by_session_async(
        self, session_id: str
    ) -> SessionTokenUsage:

        return await asyncio.to_thread(
            self._token_usage_repo.get_by_session, session_id
        )
