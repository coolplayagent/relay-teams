# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.event_log import EventLog

LOGGER = get_logger(__name__)


class StaleTaskSweeper:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        wakeup_repo: AgentWakeupRepository,
        event_log: EventLog,
        sweep_interval_seconds: float = 60.0,
        default_stale_multiplier: float = 3.0,
    ) -> None:
        self._task_repo = task_repo
        self._wakeup_repo = wakeup_repo
        self._event_log = event_log
        self._sweep_interval_seconds = sweep_interval_seconds
        self._default_stale_multiplier = default_stale_multiplier
        self._background_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._background_task is not None:
            return
        self._background_task = asyncio.create_task(
            self._sweep_loop_async(),
            name="stale-task-sweeper",
        )

    async def stop(self) -> None:
        if self._background_task is None:
            return
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:
            pass
        self._background_task = None

    async def _sweep_loop_async(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._sweep_interval_seconds)
                await self._sweep_once_async()
        except asyncio.CancelledError:
            return

    async def _sweep_once_async(self) -> None:
        try:
            running_tasks = await self._task_repo.list_running_async()
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.sweeper.list_failed",
                message="Failed to list running tasks",
                payload={"error": str(exc)},
            )
            return

        now = datetime.now(tz=timezone.utc)
        for record in running_tasks:
            lifecycle = record.envelope.lifecycle
            if lifecycle.heartbeat_interval_seconds is None:
                continue

            multiplier = lifecycle.stale_silence_multiplier
            silence_threshold = lifecycle.heartbeat_interval_seconds * multiplier
            elapsed_silence = (now - record.updated_at).total_seconds()

            if elapsed_silence <= silence_threshold:
                continue

            log_event(
                LOGGER,
                logging.WARNING,
                event="task.sweeper.stale_detected",
                message="Stale task detected by sweeper",
                payload={
                    "task_id": record.envelope.task_id,
                    "elapsed_silence": elapsed_silence,
                    "threshold": silence_threshold,
                },
            )

            error_message = "Stale task detected by sweeper"
            await self._task_repo.update_status_async(
                record.envelope.task_id,
                TaskStatus.TIMEOUT,
                error_message=error_message,
            )

            await self._event_log.emit_async(
                EventEnvelope(
                    event_type=EventType.TASK_TIMEOUT,
                    trace_id=record.envelope.trace_id,
                    session_id=record.envelope.session_id,
                    task_id=record.envelope.task_id,
                    payload_json='{"source":"stale_sweeper"}',
                )
            )

            on_timeout = lifecycle.on_timeout
            if on_timeout == TaskTimeoutAction.RETRY:
                retry_attempt = record.envelope.retry_attempt
                max_attempts = lifecycle.max_retry_attempts
                if retry_attempt >= max_attempts:
                    await self._task_repo.update_status_async(
                        record.envelope.task_id,
                        TaskStatus.FAILED,
                        error_message="Max retry attempts reached for stale task",
                    )
                    continue
                entry = AgentWakeupEntry(
                    wakeup_id=f"wk_{record.envelope.task_id}_{retry_attempt + 1}",
                    task_id=record.envelope.task_id,
                    trace_id=record.envelope.trace_id,
                    session_id=record.envelope.session_id,
                    coalesce_key=f"{record.envelope.task_id}:retry",
                    timeout_action=TaskTimeoutAction.RETRY,
                    timeout_seconds=lifecycle.timeout_seconds or 0.0,
                    attempt=retry_attempt + 1,
                    max_attempts=max_attempts,
                    status=WakeupStatus.PENDING,
                    enqueued_at=now,
                )
                await self._wakeup_repo.enqueue_async(entry)
            elif on_timeout == TaskTimeoutAction.FAIL:
                await self._task_repo.update_status_async(
                    record.envelope.task_id,
                    TaskStatus.FAILED,
                    error_message=error_message,
                )
