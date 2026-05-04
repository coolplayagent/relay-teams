# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging

from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.orchestration.task_contracts import (
    TaskExecutionServiceLike,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.event_log import EventLog

LOGGER = get_logger(__name__)


class WakeupDispatcher:
    def __init__(
        self,
        *,
        wakeup_repo: AgentWakeupRepository,
        task_repo: TaskRepository,
        task_execution_service: TaskExecutionServiceLike,
        event_log: EventLog,
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self._wakeup_repo = wakeup_repo
        self._task_repo = task_repo
        self._task_execution_service = task_execution_service
        self._event_log = event_log
        self._poll_interval_seconds = poll_interval_seconds
        self._background_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._background_task is not None:
            return
        self._background_task = asyncio.create_task(
            self._dispatch_loop_async(),
            name="wakeup-dispatcher",
        )

    async def stop(self) -> None:
        if self._background_task is None:
            return
        self._background_task.cancel()
        try:
            await self._background_task
        except asyncio.CancelledError:
            LOGGER.debug("Dispatcher background task cancelled during shutdown")
        self._background_task = None

    async def _dispatch_loop_async(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval_seconds)
                await self._dispatch_one_async()
        except asyncio.CancelledError:
            return

    async def _dispatch_one_async(self) -> None:
        entry = await self._wakeup_repo.claim_next_pending_async()
        if entry is None:
            return
        try:
            task_record = await self._task_repo.get_async(entry.task_id)
        except KeyError:
            await self._wakeup_repo.expire_async(entry.wakeup_id)
            return

        if task_record.status not in {TaskStatus.TIMEOUT, TaskStatus.STOPPED}:
            await self._wakeup_repo.expire_async(entry.wakeup_id)
            return

        log_event(
            LOGGER,
            logging.INFO,
            event="task.wakeup.redispatched",
            message="Task re-dispatched from wakeup queue",
            payload={
                "task_id": entry.task_id,
                "wakeup_id": entry.wakeup_id,
                "attempt": entry.attempt,
            },
        )

        envelope = task_record.envelope.model_copy(
            update={"retry_attempt": entry.attempt},
        )
        # Determine instance_id for retry; use a fresh identifier
        retry_instance_id = f"retry_{entry.task_id}_{entry.attempt}"
        if not envelope.role_id:
            await self._wakeup_repo.expire_async(entry.wakeup_id)
            return
        try:
            await self._task_execution_service.execute(
                instance_id=retry_instance_id,
                role_id=envelope.role_id,
                task=envelope,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.wakeup.redispatch_failed",
                message="Task re-dispatch from wakeup queue failed",
                payload={
                    "task_id": entry.task_id,
                    "wakeup_id": entry.wakeup_id,
                    "error": str(exc),
                },
            )
            return

        await self._wakeup_repo.complete_async(entry.wakeup_id)
