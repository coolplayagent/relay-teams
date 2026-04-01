# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Protocol

from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from agent_teams.sessions.runs.background_tasks.projection import (
    build_background_task_completion_message,
)
from agent_teams.sessions.runs.background_tasks.command_runtime import (
    normalize_timeout,
)
from agent_teams.workspace import WorkspaceHandle

LOGGER = get_logger(__name__)
_DEFAULT_SYNC_WAIT_MS = 1000
_MIN_SYNC_WAIT_MS = 200
_COMPLETION_RETRY_INITIAL_DELAY_SECONDS = 1.0
_COMPLETION_RETRY_MAX_DELAY_SECONDS = 30.0


class BackgroundTaskCompletionSink(Protocol):
    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None: ...


class BackgroundTaskService:
    def __init__(
        self,
        *,
        background_task_manager: BackgroundTaskManager | None,
        repository: BackgroundTaskRepository,
    ) -> None:
        self._background_task_manager = background_task_manager
        self._repository = repository
        self._completion_sink: BackgroundTaskCompletionSink | None = None
        self._completion_retry_tasks: dict[str, asyncio.Task[None]] = {}
        if self._background_task_manager is not None:
            self._background_task_manager.set_completion_listener(
                self._handle_background_task_completion
            )

    def bind_completion_sink(
        self,
        sink: BackgroundTaskCompletionSink | None,
    ) -> None:
        self._completion_sink = sink
        if sink is not None:
            self._flush_pending_completion_notifications()

    async def execute_command(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace: WorkspaceHandle,
        command: str,
        cwd: Path,
        yield_time_ms: int | None,
        timeout_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
        background: bool,
    ) -> tuple[BackgroundTaskRecord, bool]:
        manager = self._require_manager()
        timeout = normalize_timeout(timeout_ms)
        if background:
            record = await manager.start_session(
                run_id=run_id,
                session_id=session_id,
                instance_id=instance_id,
                role_id=role_id,
                tool_call_id=tool_call_id,
                workspace=workspace,
                command=command,
                cwd=cwd,
                timeout_ms=timeout,
                env=env,
                tty=tty,
                execution_mode="background",
            )
            updated, completed = await manager.interact_for_run(
                run_id=run_id,
                background_task_id=record.background_task_id,
                chars="",
                yield_time_ms=yield_time_ms,
                is_initial_poll=True,
            )
            return updated, completed

        record = await manager.start_session(
            run_id=run_id,
            session_id=session_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_call_id=tool_call_id,
            workspace=workspace,
            command=command,
            cwd=cwd,
            timeout_ms=timeout,
            env=env,
            tty=tty,
            execution_mode="foreground",
        )
        wait_ms = _normalize_sync_wait_ms(yield_time_ms)
        while True:
            updated, completed = await manager.wait_for_run(
                run_id=run_id,
                background_task_id=record.background_task_id,
                wait_ms=wait_ms,
            )
            if completed:
                return updated, True

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        manager = self._require_manager()
        return tuple(
            record
            for record in manager.list_for_run(run_id)
            if record.execution_mode == "background"
        )

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = self._require_manager().get_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Unknown background task: {background_task_id}")
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        wait_ms: int,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        if not record.is_active:
            return self._mark_completion_consumed(record), True
        updated, completed = await self._require_manager().wait_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
            wait_ms=wait_ms,
        )
        if not completed:
            return updated, False
        return self._mark_completion_consumed(updated), True

    async def stop_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        _ = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        return await self._require_manager().stop_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )

    async def _handle_background_task_completion(
        self, record: BackgroundTaskRecord
    ) -> None:
        await asyncio.sleep(0)
        delivered = self._attempt_completion_delivery(record.background_task_id)
        if not delivered and self._completion_sink is not None:
            self._schedule_completion_retry(record.background_task_id)

    def _attempt_completion_delivery(self, background_task_id: str) -> bool:
        current = self._repository.get(background_task_id)
        if current is None:
            return True
        if not self._should_notify_completion(current):
            return True
        if self._completion_sink is None:
            return False
        message = build_background_task_completion_message(current)
        try:
            self._completion_sink.handle_background_task_completion(
                record=current,
                message=message,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="background_task.notification_failed",
                message="Failed to deliver background task completion notification",
                payload={"background_task_id": current.background_task_id},
                exc_info=exc,
            )
            return False
        _ = self._mark_completion_consumed(current)
        return True

    def _flush_pending_completion_notifications(self) -> None:
        for record in self._repository.list_all():
            if self._should_notify_completion(record):
                delivered = self._attempt_completion_delivery(record.background_task_id)
                if not delivered and self._completion_sink is not None:
                    self._schedule_completion_retry(
                        record.background_task_id,
                        initial_delay_seconds=0.0,
                    )

    def _schedule_completion_retry(
        self,
        background_task_id: str,
        *,
        initial_delay_seconds: float = _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
    ) -> None:
        existing = self._completion_retry_tasks.get(background_task_id)
        if existing is not None and not existing.done():
            return
        try:
            self._completion_retry_tasks[background_task_id] = asyncio.create_task(
                self._retry_completion_delivery(
                    background_task_id,
                    initial_delay_seconds=initial_delay_seconds,
                )
            )
        except RuntimeError:
            return

    async def _retry_completion_delivery(
        self,
        background_task_id: str,
        *,
        initial_delay_seconds: float,
    ) -> None:
        delay_seconds = initial_delay_seconds
        try:
            while True:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                if self._attempt_completion_delivery(background_task_id):
                    return
                if self._completion_sink is None:
                    return
                delay_seconds = min(
                    _COMPLETION_RETRY_MAX_DELAY_SECONDS,
                    max(
                        _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
                        delay_seconds * 2
                        if delay_seconds > 0
                        else _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
                    ),
                )
        finally:
            task = self._completion_retry_tasks.get(background_task_id)
            if task is asyncio.current_task():
                self._completion_retry_tasks.pop(background_task_id, None)

    def _mark_completion_consumed(
        self, record: BackgroundTaskRecord
    ) -> BackgroundTaskRecord:
        if not self._should_notify_completion(record):
            return record
        completed_at = datetime.now(tz=timezone.utc)
        return self._repository.upsert(
            record.model_copy(
                update={
                    "completion_notified_at": completed_at,
                    "updated_at": completed_at,
                }
            )
        )

    def _should_notify_completion(self, record: BackgroundTaskRecord) -> bool:
        return (
            record.execution_mode == "background"
            and not record.is_active
            and record.status != BackgroundTaskStatus.STOPPED
            and record.completion_notified_at is None
        )

    def _require_manager(self) -> BackgroundTaskManager:
        if self._background_task_manager is None:
            raise RuntimeError("Background task service is not configured")
        return self._background_task_manager


def _normalize_sync_wait_ms(wait_ms: int | None) -> int:
    if wait_ms is None:
        return _DEFAULT_SYNC_WAIT_MS
    if wait_ms < 1:
        raise ValueError("yield_time_ms must be >= 1")
    return max(_MIN_SYNC_WAIT_MS, wait_ms)
