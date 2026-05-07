# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import (
    TaskSpecStrictness,
    TaskStatus,
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.models import (
    TaskArtifactEntry,
    TaskEnvelope,
    TaskHandoff,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.tools.runtime.guardrails import RuntimeGuardrailPolicy
from relay_teams.tools.runtime.policy import ToolApprovalPolicy

LOGGER = get_logger(__name__)
TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 5.0


class AuditContext(BaseModel):
    """Context carried across control/compute boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str = ""
    run_id: str = ""
    task_id: str = ""
    session_id: str = ""
    instance_id: str = ""
    role_id: str = ""


class ResolvedPolicyContext(BaseModel):
    """Resolved policy decisions injected from control plane into compute."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    guardrail_policy: RuntimeGuardrailPolicy
    approval_policy: ToolApprovalPolicy
    strictness: TaskSpecStrictness = TaskSpecStrictness.MEDIUM
    lease_expires_at: datetime | None = None


class TaskControlHarness:
    """Control plane harness for task lifecycle management.

    Encapsulates lifecycle transitions, timeout management, heartbeat
    scheduling, lease/claim management, wakeup enqueue, and artifact
    construction so TaskExecutionService stays thin.
    """

    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        run_runtime_repo: RunRuntimeRepository,
        event_bus: EventLog,
        wakeup_repo: AgentWakeupRepository | None = None,
        artifact_repo: TaskArtifactRepository | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._run_runtime_repo = run_runtime_repo
        self._event_bus = event_bus
        self._wakeup_repo = wakeup_repo
        self._artifact_repo = artifact_repo

    async def transition_to_running(
        self,
        task: TaskEnvelope,
        instance_id: str,
        _role_id: str,
    ) -> None:
        """Handle CREATED/ASSIGNED -> RUNNING transition."""
        record = await self._task_repo.get_async(task.task_id)
        if record.status == TaskStatus.RUNNING:
            return
        if record.status not in (
            TaskStatus.CREATED,
            TaskStatus.ASSIGNED,
        ):
            raise ValueError(
                f"Task {task.task_id} cannot transition from "
                f"{record.status.value} to RUNNING"
            )
        await self._task_repo.update_status_async(
            task.task_id,
            TaskStatus.RUNNING,
            assigned_instance_id=instance_id,
        )

    async def start_heartbeat(
        self,
        task: TaskEnvelope,
        instance_id: str,
        _role_id: str,
        worker: asyncio.Task[object],
    ) -> asyncio.Task[None] | None:
        """Start a heartbeat task that periodically updates updated_at."""
        interval = task.lifecycle.heartbeat_interval_seconds
        if interval is None:
            return None

        async def _heartbeat_loop() -> None:
            try:
                while not worker.done():
                    await asyncio.sleep(interval)
                    if worker.done():
                        return
                    try:
                        await self._task_repo.heartbeat_running_async(
                            task_id=task.task_id,
                            assigned_instance_id=instance_id,
                        )
                    except (RuntimeError, ValueError, KeyError):
                        log_event(
                            LOGGER,
                            logging.DEBUG,
                            event="task.heartbeat.failed",
                            message="Task heartbeat update failed",
                            payload={
                                "task_id": task.task_id,
                                "instance_id": instance_id,
                            },
                        )
            except asyncio.CancelledError:
                return

        return asyncio.create_task(
            _heartbeat_loop(),
            name=f"heartbeat-{task.task_id[:12]}",
        )

    async def handle_timeout(
        self,
        task: TaskEnvelope,
        instance_id: str,
        _role_id: str,
        worker: asyncio.Task[TaskExecutionResult],
        timeout_cancellation: asyncio.Event,
        timeout_seconds: float,
    ) -> TaskExecutionResult:
        """Handle timeout after worker cancellation grace period."""
        timeout_cancellation.set()
        if not worker.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=TIMEOUT_WORKER_CANCEL_GRACE_SECONDS,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                worker.cancel()
                try:
                    await worker
                except (asyncio.CancelledError, Exception):
                    # Intentional: worker was just cancelled; swallow cleanup errors.
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        event="harness.worker_cancel_suppressed",
                        message="Worker cleanup error suppressed after cancellation",
                    )

            handoff = _timeout_handoff(task, timeout_seconds)
            await self._task_repo.update_status_async(
                task.task_id,
                TaskStatus.TIMEOUT,
                assigned_instance_id=instance_id,
                error_message=f"Task timed out after {timeout_seconds}s",
            )
            await self._maybe_enqueue_retry(task)
            return TaskExecutionResult(
                output=handoff.reason or "Task timed out",
                error_message=f"Task timed out after {timeout_seconds}s",
            )

        try:
            return await worker
        except Exception as exc:
            return TaskExecutionResult(
                output=str(exc),
                error_message=str(exc),
            )

    async def claim_and_lease(
        self,
        task: TaskEnvelope,
        instance_id: str,
        claim_token: str,
    ) -> bool:
        """Atomically claim a task with a lease."""
        timeout_seconds = task.lifecycle.timeout_seconds or 3600.0
        return await self._task_repo.claim_task_async(
            task_id=task.task_id,
            lease_owner=instance_id,
            claim_token=claim_token,
            lease_duration_seconds=timeout_seconds,
        )

    async def enqueue_retry_wakeup(
        self,
        task: TaskEnvelope,
        attempt: int,
    ) -> bool:
        """Enqueue a timeout-retry wakeup entry."""
        if self._wakeup_repo is None:
            return False
        now = datetime.now(tz=timezone.utc)
        lifecycle = task.lifecycle
        entry = AgentWakeupEntry(
            wakeup_id=f"wk_retry_{task.task_id}_{attempt}",
            task_id=task.task_id,
            trace_id=task.trace_id,
            session_id=task.session_id,
            coalesce_key=f"{task.task_id}:retry",
            timeout_action=lifecycle.on_timeout,
            timeout_seconds=lifecycle.timeout_seconds or 0.0,
            attempt=attempt,
            max_attempts=lifecycle.max_retry_attempts,
            status=WakeupStatus.PENDING,
            enqueued_at=now,
            wake_reason=WakeupReason.TIMEOUT_RETRY,
            target_role=task.role_id or "",
        )
        return await self._wakeup_repo.enqueue_async(entry)

    async def append_artifact_entry(
        self,
        task_id: str,
        entry: TaskArtifactEntry,
    ) -> int | None:
        """Append an artifact entry. Returns entry count or None if repo unavailable."""
        if self._artifact_repo is None:
            return None
        artifact = await asyncio.to_thread(self._artifact_repo.get_artifact, task_id)
        if artifact is None:
            return None
        await asyncio.to_thread(self._artifact_repo.append_entry, task_id, entry)
        updated = await asyncio.to_thread(self._artifact_repo.get_artifact, task_id)
        return len(updated.entries) if updated else None

    async def _maybe_enqueue_retry(self, task: TaskEnvelope) -> None:
        lifecycle = task.lifecycle
        if lifecycle.on_timeout != TaskTimeoutAction.RETRY:
            return
        next_attempt = task.retry_attempt + 1
        if next_attempt > lifecycle.max_retry_attempts:
            return
        await self.enqueue_retry_wakeup(task, next_attempt)


def _timeout_handoff(task: TaskEnvelope, timeout_seconds: float) -> TaskHandoff:
    return TaskHandoff(
        reason=f"Task {task.task_id} timed out after {timeout_seconds}s",
        next_steps=("Review partial output and re-dispatch if needed.",),
    )
