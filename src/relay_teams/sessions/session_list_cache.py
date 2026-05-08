# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
from time import monotonic
from typing import Any

from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRecord
from relay_teams.sessions.session_models import SessionMode, SessionRecord

LOGGER = get_logger(__name__)
LIST_SESSIONS_CACHE_MS_ENV = "RELAY_TEAMS_LIST_SESSIONS_CACHE_MS"
DEFAULT_LIST_SESSIONS_CACHE_MS = 1000
LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS = 2.0
LIST_SESSIONS_STALE_REFRESH_AFTER_SECONDS = 2.0


def resolve_positive_int_env(name: str, default: int) -> int:
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


class SessionListCacheMixin:
    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)

    def _invalidate_list_sessions_cache(self) -> None:
        with self._list_sessions_cache_lock:
            self._list_sessions_cache_version += 1
            self._list_sessions_cache_dirty = True

    def _merge_record_into_list_sessions_cache(self, record: SessionRecord) -> None:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
            if cached is None:
                self._list_sessions_cache_dirty = True
                return
            records = tuple(
                cached_record
                for cached_record in cached[1]
                if cached_record.session_id != record.session_id
            )
            self._list_sessions_cache = (now, (record, *records))
            self._list_sessions_cache_dirty = dirty

    def _remove_record_from_list_sessions_cache(self, session_id: str) -> None:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
            if cached is None:
                return
            self._list_sessions_cache = (
                now,
                tuple(
                    record for record in cached[1] if record.session_id != session_id
                ),
            )
            self._list_sessions_cache_dirty = dirty

    def _get_session_from_list_cache(
        self,
        session_id: str,
        *,
        allow_stale: bool,
    ) -> SessionRecord | None:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
        if cached is None:
            return None
        if not allow_stale and (
            dirty or now - cached[0] > self._list_sessions_cache_ttl_seconds
        ):
            return None
        for record in cached[1]:
            if record.session_id == session_id:
                return record
        return None

    def _ensure_list_sessions_refresh_task_if_stale(self) -> None:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
        if cached is None:
            return
        if dirty or now - cached[0] > self._list_sessions_cache_ttl_seconds:
            self._ensure_list_sessions_refresh_task()

    def list_sessions(self) -> tuple[SessionRecord, ...]:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
        if (
            cached is not None
            and not dirty
            and now - cached[0] <= self._list_sessions_cache_ttl_seconds
        ):
            return cached[1]
        return self._refresh_list_sessions_cache()

    def _refresh_list_sessions_cache(
        self,
        *,
        expected_cache_version: int | None = None,
    ) -> tuple[SessionRecord, ...]:
        sessions = self._session_repo.list_all()
        session_ids = tuple(record.session_id for record in sessions)
        runtimes_by_session: dict[str, tuple[RunRuntimeRecord, ...]] = (
            self._run_runtime_repo.list_by_session_ids(session_ids)
        )
        background_tasks_by_session: dict[str, tuple[BackgroundTaskRecord, ...]] = (
            self._background_task_repository.list_by_session_ids(session_ids)
            if self._background_task_repository is not None
            else {}
        )
        excluded_run_ids_by_session = self._subagent_run_ids_by_session_ids(
            session_ids=session_ids,
            runtimes_by_session=runtimes_by_session,
            background_tasks_by_session=background_tasks_by_session,
        )
        active_background_run_ids = self._active_background_run_ids(
            background_tasks_by_session,
        )
        first_intent_titles: dict[str, str] = (
            self._run_intent_repo.first_titles_by_session_ids(session_ids)
            if self._run_intent_repo is not None
            else {}
        )
        session_ids_needing_message_titles = tuple(
            record.session_id
            for record in sessions
            if record.session_id not in first_intent_titles
        )
        first_user_messages = self._message_repo.first_user_messages_by_session_ids(
            session_ids_needing_message_titles
        )
        normal_session_ids = tuple(
            record.session_id
            for record in sessions
            if record.session_mode == SessionMode.NORMAL
        )
        subagent_counts = self._agent_repo.count_normal_mode_subagents_by_session_ids(
            normal_session_ids
        )
        selected_by_session: dict[str, tuple[str, RunRuntimeRecord]] = {}
        for session_id in session_ids:
            selected = self._select_active_run_from_preloaded(
                session_id=session_id,
                runtimes=runtimes_by_session.get(session_id, ()),
                excluded_run_ids=excluded_run_ids_by_session.get(session_id, set()),
                active_background_run_ids=active_background_run_ids,
            )
            if selected is not None:
                selected_by_session[session_id] = selected
        selected_run_ids = tuple(
            dict.fromkeys(run_id for run_id, _runtime in selected_by_session.values())
        )
        approval_counts = self._approval_ticket_repo.count_open_by_run_ids(
            selected_run_ids
        )
        question_counts = (
            self._user_question_repo.count_open_by_run_ids(selected_run_ids)
            if self._user_question_repo is not None
            else {}
        )
        enriched: list[SessionRecord] = []
        for record in sessions:
            record = self._with_auto_session_title_from_preloaded(
                record,
                first_intent_titles=first_intent_titles,
                first_user_messages=first_user_messages,
            )
            selected = selected_by_session.get(record.session_id)
            subagent_session_count = subagent_counts.get(record.session_id, 0)
            runtimes = runtimes_by_session.get(record.session_id, ())
            excluded_run_ids = excluded_run_ids_by_session.get(
                record.session_id,
                set(),
            )
            if selected is None:
                enriched.append(
                    self._with_terminal_run_projection_from_preloaded(
                        record.model_copy(
                            update={
                                "subagent_session_count": subagent_session_count,
                            }
                        ),
                        runtimes=runtimes,
                        excluded_run_ids=excluded_run_ids,
                    )
                )
                continue
            run_id, runtime = selected
            approval_count = approval_counts.get(run_id, 0)
            question_count = question_counts.get(run_id, 0)
            enriched.append(
                self._with_terminal_run_projection_from_preloaded(
                    record.model_copy(
                        update={
                            "has_active_run": True,
                            "active_run_id": run_id,
                            "active_run_status": runtime.status.value,
                            "active_run_phase": self._public_phase(
                                runtime,
                                approval_count,
                                question_count,
                            ),
                            "pending_tool_approval_count": approval_count,
                            "subagent_session_count": subagent_session_count,
                        }
                    ),
                    runtimes=runtimes,
                    excluded_run_ids=excluded_run_ids,
                )
            )
        result = tuple(enriched)
        with self._list_sessions_cache_lock:
            if (
                expected_cache_version is not None
                and expected_cache_version != self._list_sessions_cache_version
            ):
                cached = self._list_sessions_cache
                return cached[1] if cached is not None else result
            self._list_sessions_cache = (monotonic(), result)
            self._list_sessions_cache_dirty = False
        return result

    async def list_sessions_async(self) -> tuple[SessionRecord, ...]:
        now = monotonic()
        with self._list_sessions_cache_lock:
            cached = self._list_sessions_cache
            dirty = self._list_sessions_cache_dirty
        if (
            cached is not None
            and not dirty
            and now - cached[0] <= self._list_sessions_cache_ttl_seconds
        ):
            return cached[1]
        if cached is not None:
            self._ensure_list_sessions_refresh_task(
                force=now - cached[0] >= LIST_SESSIONS_STALE_REFRESH_AFTER_SECONDS,
            )
            log_event(
                LOGGER,
                logging.DEBUG,
                event="session.list_cache.stale_hit",
                message="Returned stale session list cache while refreshing",
                payload={
                    "snapshot_age_ms": int((now - cached[0]) * 1000),
                    "session_count": len(cached[1]),
                },
            )
            return cached[1]
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.list_sessions),
                timeout=LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._ensure_list_sessions_refresh_task()
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.list_cache.cold_miss_timeout",
                message="Session list cold cache build exceeded fast-read budget",
                payload={
                    "timeout_ms": int(LIST_SESSIONS_COLD_MISS_TIMEOUT_SECONDS * 1000),
                },
            )
            return ()

    def _ensure_list_sessions_refresh_task(self, *, force: bool = False) -> None:
        task = self._list_sessions_refresh_task
        if task is not None and not task.done():
            return
        now = monotonic()
        elapsed_ms = int((now - self._list_sessions_refresh_started_monotonic) * 1000)
        if not force and elapsed_ms < int(self._list_sessions_cache_ttl_seconds * 1000):
            return
        self._list_sessions_refresh_started_monotonic = now
        with self._list_sessions_cache_lock:
            expected_cache_version = self._list_sessions_cache_version
        task = asyncio.create_task(
            self._refresh_list_sessions_cache_async(
                expected_cache_version=expected_cache_version,
            )
        )
        self._list_sessions_refresh_task = task
        task.add_done_callback(self._observe_list_sessions_refresh_result)

    async def _refresh_list_sessions_cache_async(
        self,
        *,
        expected_cache_version: int,
    ) -> None:
        started = monotonic()
        result = await asyncio.to_thread(
            self._refresh_list_sessions_cache,
            expected_cache_version=expected_cache_version,
        )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="session.list_cache.refreshed",
            message="Refreshed session list cache",
            payload={
                "session_count": len(result),
                "refresh_ms": int((monotonic() - started) * 1000),
            },
        )

    def _observe_list_sessions_refresh_result(
        self,
        task: asyncio.Task[None],
    ) -> None:
        if self._list_sessions_refresh_task is task:
            self._list_sessions_refresh_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="session.list_cache.refresh_failed",
                message="Session list cache refresh failed",
                payload={
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        with self._list_sessions_cache_lock:
            dirty = self._list_sessions_cache_dirty
        if dirty:
            self._ensure_list_sessions_refresh_task(force=True)
