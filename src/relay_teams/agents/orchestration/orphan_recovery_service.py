# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from datetime import datetime, timezone

from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.enums import (
    TaskStatus,
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.event_log import EventLog

LOGGER = get_logger(__name__)


class OrphanRecoveryService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        wakeup_repo: AgentWakeupRepository,
        agent_repo: AgentInstanceRepository,
        event_log: EventLog,
    ) -> None:
        self._task_repo = task_repo
        self._wakeup_repo = wakeup_repo
        self._agent_repo = agent_repo
        self._event_log = event_log

    async def recover_orphans_async(self) -> int:
        recovered_count = 0
        try:
            running_tasks = await self._task_repo.list_running_async()
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.orphan_recovery.list_failed",
                message="Failed to list running tasks for orphan recovery",
                payload={"error": str(exc)},
            )
            return 0

        now = datetime.now(tz=timezone.utc)
        for task in running_tasks:
            instance_id = task.assigned_instance_id
            if not instance_id:
                continue

            try:
                instance = await self._agent_repo.get_instance_async(instance_id)
            except KeyError:
                instance = None

            instance_failed = instance is None or str(instance.status) in (
                "failed",
                "interrupted",
            )

            lease_expired = False
            lease_expires_at = task.envelope.lease_expires_at
            if lease_expires_at is not None and lease_expires_at < now:
                lease_expired = True

            if not instance_failed and not lease_expired:
                continue

            orphan_reason = "instance_failed" if instance_failed else "lease_expired"

            log_event(
                LOGGER,
                logging.WARNING,
                event="task.orphan_recovery.orphan_detected",
                message="Orphan task detected during recovery",
                payload={
                    "task_id": task.envelope.task_id,
                    "instance_id": instance_id,
                    "instance_status": str(instance.status) if instance else "unknown",
                    "orphan_reason": orphan_reason,
                },
            )

            error_message = "Orphan task recovered after service restart"
            await self._task_repo.update_status_async(
                task.envelope.task_id,
                TaskStatus.TIMEOUT,
                error_message=error_message,
            )

            await self._event_log.emit_async(
                EventEnvelope(
                    event_type=EventType.ORPHAN_RECOVERED,
                    trace_id=task.envelope.trace_id,
                    session_id=task.envelope.session_id,
                    task_id=task.envelope.task_id,
                )
            )

            lifecycle = task.envelope.lifecycle
            if lifecycle.on_timeout == TaskTimeoutAction.RETRY:
                retry_attempt = task.envelope.retry_attempt
                max_attempts = lifecycle.max_retry_attempts
                if retry_attempt < max_attempts:
                    entry = AgentWakeupEntry(
                        wakeup_id=f"wk_orphan_{task.envelope.task_id}",
                        task_id=task.envelope.task_id,
                        trace_id=task.envelope.trace_id,
                        session_id=task.envelope.session_id,
                        coalesce_key=f"{task.envelope.task_id}:retry",
                        timeout_action=TaskTimeoutAction.RETRY,
                        timeout_seconds=lifecycle.timeout_seconds or 0.0,
                        attempt=retry_attempt + 1,
                        max_attempts=max_attempts,
                        status=WakeupStatus.PENDING,
                        enqueued_at=now,
                        wake_reason=WakeupReason.ORPHAN_RECOVERY,
                    )
                    await self._wakeup_repo.enqueue_async(entry)
                else:
                    await self._task_repo.update_status_async(
                        task.envelope.task_id,
                        TaskStatus.FAILED,
                        error_message="Max retry attempts reached for orphan task",
                    )
            elif lifecycle.on_timeout == TaskTimeoutAction.FAIL:
                await self._task_repo.update_status_async(
                    task.envelope.task_id,
                    TaskStatus.FAILED,
                    error_message=error_message,
                )

            recovered_count += 1

        return recovered_count
