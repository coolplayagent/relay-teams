# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.harnesses.persistence_harness import (
    TaskPersistenceHarness,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import (
    TaskArtifactPhase,
    TaskSpecStrictness,
    TaskStatus,
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import (
    TaskArtifactEntry,
    TaskEnvelope,
    TaskHandoff,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.assistant_errors import (
    RunCompletionReason,
    build_assistant_error_message,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailPolicy,
    generate_runtime_guardrail_report_async,
)
from relay_teams.tools.runtime.policy import ToolApprovalPolicy

LOGGER = get_logger(__name__)
TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 5.0
TASK_TIMEOUT_PROGRESS_POLL_MAX_SECONDS = 1.0
TASK_TIMEOUT_PROGRESS_POLL_MIN_SECONDS = 0.001


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
        # Phase 2 (AO-1-C1) additions:
        message_repo: MessageRepository | None = None,
        shared_store: SharedStateRepository | None = None,
        run_event_hub: RunEventHub | None = None,
        run_control_manager: RunControlManager | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._run_runtime_repo = run_runtime_repo
        self._event_bus = event_bus
        self._wakeup_repo = wakeup_repo
        self._artifact_repo = artifact_repo
        self._message_repo = message_repo
        self._shared_store = shared_store
        self._run_event_hub = run_event_hub
        self._run_control_manager = run_control_manager

    # ── Phase 1 methods (unchanged) ────────────────────────────────────

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

    def start_heartbeat(
        self,
        task: TaskEnvelope,
        instance_id: str,
        worker: asyncio.Task[object],
    ) -> asyncio.Task[None] | None:
        """Start a heartbeat task that periodically updates updated_at.

        Extended in Phase 2 with full recovery/stop logic migrated from
        TaskExecutionService._heartbeat_task_until_done.
        """
        interval = task.lifecycle.heartbeat_interval_seconds
        if interval is None:
            return None

        async def _heartbeat_loop() -> None:
            try:
                while not worker.done():
                    await asyncio.sleep(interval)
                    if worker.done():
                        return
                    updated = await self._task_repo.heartbeat_running_async(
                        task_id=task.task_id,
                        assigned_instance_id=instance_id,
                    )
                    if not updated:
                        should_stop = await self._should_stop_heartbeat_after_skip(
                            task=task,
                            instance_id=instance_id,
                        )
                        if should_stop:
                            return
                        continue
                    log_event(
                        LOGGER,
                        logging.DEBUG,
                        event="task.execution.heartbeat",
                        message="Task heartbeat recorded",
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

            handoff = _timeout_handoff(task=task, timeout_seconds=timeout_seconds)
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
        artifact = self._artifact_repo.get_artifact(task_id)
        if artifact is None:
            return None
        self._artifact_repo.append_entry(task_id, entry)
        updated = self._artifact_repo.get_artifact(task_id)
        return len(updated.entries) if updated else None

    # ── Phase 2 (AO-1-C1) new methods ──────────────────────────────────

    async def transition_task_to_running(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        is_coordinator: bool,
    ) -> None:
        """Atomic initial state transition for task execution start.

        Migrated from TaskExecutionService._execute_inner L352-392.
        Performs: mark agent RUNNING + update task RUNNING + ensure/update
        run_runtime + emit TASK_STARTED event.
        """
        await self._agent_repo.mark_status_async(instance_id, InstanceStatus.RUNNING)
        _ = await self._task_repo.update_status_async(
            task.task_id,
            TaskStatus.RUNNING,
            assigned_instance_id=instance_id,
        )
        await self._run_runtime_repo.ensure_async(
            run_id=task.trace_id,
            session_id=task.session_id,
            root_task_id=task.parent_task_id or task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
        )
        await self._run_runtime_repo.update_async(
            task.trace_id,
            status=RunRuntimeStatus.RUNNING,
            phase=(
                RunRuntimePhase.COORDINATOR_RUNNING
                if is_coordinator
                else RunRuntimePhase.SUBAGENT_RUNNING
            ),
            active_instance_id=instance_id,
            active_task_id=task.task_id,
            active_role_id=role_id,
            active_subagent_instance_id=(None if is_coordinator else instance_id),
            last_error=None,
        )
        await self._event_bus.emit_async(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )

    def initialize_task_artifact(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
    ) -> None:
        """Create artifact container + SPEC phase entry.

        Migrated from TaskExecutionService._execute_inner L395-421.
        Internally try/except each operation and log warning; never raises.
        """
        if self._artifact_repo is None:
            return
        try:
            self._artifact_repo.ensure_artifact(
                task_id=task.task_id,
                spec_artifact_id=task.spec_artifact_id or "",
            )
            self._artifact_repo.append_entry(
                task_id=task.task_id,
                entry=TaskArtifactEntry(
                    entry_id=f"start-{task.task_id}",
                    phase=TaskArtifactPhase.SPEC,
                    timestamp=datetime.now(tz=timezone.utc).isoformat(),
                    role_id=role_id,
                    instance_id=instance_id,
                    event_type="task_started",
                    description="Task execution started",
                    payload_json=task.model_dump_json(),
                ),
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="artifact.create_failed",
                message="Failed to create task artifact",
                payload={"task_id": task.task_id, "error": str(exc)},
            )

    async def publish_guardrail_report(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
    ) -> None:
        """Generate and publish a runtime guardrail report.

        Migrated from TaskExecutionService._publish_runtime_guardrail_report_async
        L709-751. Emits via run_event_hub or event_bus SSE.
        Never raises; logs warning on failure.
        """
        if self._shared_store is None:
            return
        try:
            report = await generate_runtime_guardrail_report_async(
                shared_store=self._shared_store,
                task_id=task.task_id,
                run_id=task.trace_id,
                session_id=task.session_id,
                role_id=role_id,
            )
            event = RunEvent(
                session_id=task.session_id,
                run_id=task.trace_id,
                trace_id=task.trace_id,
                task_id=task.task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=RunEventType.RUNTIME_GUARDRAIL_REPORT,
                payload_json=report.model_dump_json(),
            )
            if self._run_event_hub is not None:
                _ = await self._run_event_hub.publish_async(event)
            else:
                _ = await self._event_bus.emit_run_event_async(event)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="task.execution.guardrail_report_failed",
                message="Runtime guardrail report could not be generated",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )

    async def complete_task_timeout(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        timeout_seconds: float,
    ) -> TaskExecutionResult:
        """Complete timeout handling: status updates, handoff, retry enqueue.

        Migrated from TaskExecutionService._complete_task_timeout_async
        L884-1020. Returns TaskExecutionResult or raises
        RecoverableRunPauseError for paused timeouts.
        """
        timeout_action = task.lifecycle.on_timeout
        task_status = _timeout_task_status(timeout_action)
        instance_status = _timeout_instance_status(timeout_action)
        runtime_status = _timeout_runtime_status(timeout_action)
        runtime_phase = _timeout_runtime_phase(timeout_action)
        paused_timeout = timeout_action != TaskTimeoutAction.FAIL
        error_message = (
            f"Task timed out after {timeout_seconds:g}s "
            f"(on_timeout={timeout_action.value})"
        )
        assistant_message = build_assistant_error_message(
            error_code="task_timeout",
            error_message=error_message,
        )
        current = await self._task_repo.get_async(task.task_id)
        handoff = _timeout_handoff(
            task=current.envelope, timeout_seconds=timeout_seconds
        )
        await self._task_repo.update_envelope_async(
            task.task_id,
            current.envelope.model_copy(update={"handoff": handoff}),
        )
        await self._task_repo.update_status_async(
            task.task_id,
            task_status,
            assigned_instance_id=instance_id,
            result=assistant_message,
            error_message=error_message,
        )
        await self._agent_repo.mark_status_async(instance_id, instance_status)
        await self._run_runtime_repo.ensure_async(
            run_id=task.trace_id,
            session_id=task.session_id,
            root_task_id=task.parent_task_id or task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.IDLE,
        )
        await self._mark_runtime_after_terminal_task_update_async(
            run_id=task.trace_id,
            terminal_task_id=task.task_id,
            status=runtime_status,
            phase=runtime_phase,
            active_instance_id=instance_id if paused_timeout else None,
            active_task_id=task.task_id if paused_timeout else None,
            active_role_id=role_id if paused_timeout else None,
            active_subagent_instance_id=instance_id if paused_timeout else None,
            last_error=error_message,
        )
        await self._event_bus.emit_async(
            EventEnvelope(
                event_type=EventType.TASK_TIMEOUT,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json=handoff.model_dump_json(),
            )
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="task.execution.timeout",
            message="Task execution timed out",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "timeout_seconds": timeout_seconds,
                "on_timeout": timeout_action.value,
                "task_status": task_status.value,
                "instance_status": instance_status.value,
                "runtime_status": runtime_status.value,
                "runtime_phase": runtime_phase.value,
            },
        )
        if timeout_action == TaskTimeoutAction.RETRY:
            lifecycle = task.lifecycle
            retry_attempt = task.retry_attempt
            max_attempts = lifecycle.max_retry_attempts
            if retry_attempt < max_attempts and self._wakeup_repo is not None:
                now = datetime.now(tz=timezone.utc)
                entry = AgentWakeupEntry(
                    wakeup_id=f"wk_{task.task_id}_{retry_attempt + 1}",
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    coalesce_key=f"{task.task_id}:retry",
                    timeout_action=TaskTimeoutAction.RETRY,
                    timeout_seconds=lifecycle.timeout_seconds or 0.0,
                    attempt=retry_attempt + 1,
                    max_attempts=max_attempts,
                    status=WakeupStatus.PENDING,
                    enqueued_at=now,
                )
                await self._wakeup_repo.enqueue_async(entry)
                log_event(
                    LOGGER,
                    logging.INFO,
                    event="task.execution.timeout_retry_enqueued",
                    message="Retry wakeup enqueued for timed-out task",
                    payload={
                        "task_id": task.task_id,
                        "attempt": retry_attempt + 1,
                        "max_attempts": max_attempts,
                    },
                )
        if paused_timeout:
            raise RecoverableRunPauseError(
                RecoverableRunPausePayload(
                    run_id=task.trace_id,
                    trace_id=task.trace_id,
                    task_id=task.task_id,
                    session_id=task.session_id,
                    instance_id=instance_id,
                    role_id=role_id,
                    error_code="task_timeout",
                    error_message=error_message,
                    retries_used=0,
                    total_attempts=1,
                    runtime_phase=runtime_phase,
                    assistant_message=assistant_message,
                )
            )
        return TaskExecutionResult(
            output=assistant_message,
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code="task_timeout",
            error_message=error_message,
        )

    async def complete_timeout_after_worker_cancel(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        timeout_seconds: float,
        worker: asyncio.Task[TaskExecutionResult],
        timeout_cancellation: asyncio.Event,
    ) -> TaskExecutionResult:
        """Cancel worker and complete timeout handling.

        Migrated from TaskExecutionService._complete_timeout_after_worker_cancel_async
        L1022-1051.
        """
        from relay_teams.agents.orchestration.task_execution_service import (
            cancel_and_wait,
        )

        timeout_cancellation.set()
        cancel_result = await cancel_and_wait(
            worker,
            suppress_exceptions=True,
            task_name="task_worker",
            timeout_seconds=TIMEOUT_WORKER_CANCEL_GRACE_SECONDS,
            context={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
            },
        )
        if cancel_result is not None:
            return cancel_result  # type: ignore[return-value]
        return await self.complete_task_timeout(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            timeout_seconds=timeout_seconds,
        )

    async def persist_cancelled_execution(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        is_coordinator: bool,
    ) -> tuple[bool, bool]:
        """Persist state after a cancelled execution.

        Migrated from TaskExecutionService._persist_cancelled_execution_async
        L1055-1131. Returns (stopped: bool, paused_subagent: bool).
        """
        paused_subagent = False
        if self._run_control_manager is not None:
            run_stop_requested = self._run_control_manager.is_run_stop_requested(
                task.trace_id
            )
            subagent_stop_requested = (
                self._run_control_manager.is_subagent_stop_requested(
                    run_id=task.trace_id,
                    instance_id=instance_id,
                )
            )
            stopped = await self._run_control_manager.handle_instance_cancelled_async(
                task=task,
                instance_id=instance_id,
            )
            paused_subagent = (
                stopped
                and not is_coordinator
                and subagent_stop_requested
                and not run_stop_requested
            )
        else:
            stopped = False
            await self._task_repo.update_status_async(
                task.task_id,
                TaskStatus.FAILED,
                error_message="Task cancelled",
            )
            await self._agent_repo.mark_status_async(instance_id, InstanceStatus.FAILED)
            await self._event_bus.emit_async(
                EventEnvelope(
                    event_type=EventType.TASK_FAILED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
        last_error = "Task stopped by user" if stopped else "Task cancelled"
        if paused_subagent:
            if not await self._persistence_harness_async().promote_running_runtime_lane_async(  # noqa: E501
                run_id=task.trace_id,
                terminal_task_id=task.task_id,
                last_error=last_error,
            ):
                await self._run_runtime_repo.update_async(
                    task.trace_id,
                    status=RunRuntimeStatus.STOPPED,
                    phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
                    active_instance_id=None,
                    active_task_id=task.task_id,
                    active_role_id=role_id,
                    active_subagent_instance_id=instance_id,
                    last_error=last_error,
                )
        else:
            await self._mark_runtime_after_terminal_task_update_async(
                run_id=task.trace_id,
                terminal_task_id=task.task_id,
                status=(
                    RunRuntimeStatus.STOPPED if stopped else RunRuntimeStatus.FAILED
                ),
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=last_error,
            )
        return stopped, paused_subagent

    async def wait_for_worker_with_progress_timeout(
        self,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        worker: asyncio.Task[TaskExecutionResult],
        timeout_seconds: float,
    ) -> bool:
        """Progress-aware timeout wait. Extends deadline on new messages.

        Migrated from TaskExecutionService._wait_for_worker_with_progress_timeout_async
        L276-327. Returns True if worker completed, False if timeout expired.
        """
        if self._message_repo is None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=timeout_seconds,
                )
                return True
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return False
        latest_message_id = await self._message_repo.get_latest_task_message_id_async(
            task_id=task.task_id,
            instance_id=instance_id,
        )
        deadline = time.monotonic() + timeout_seconds
        poll_seconds = _timeout_progress_poll_seconds(timeout_seconds)
        while True:
            if worker.done():
                return True
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                return False
            completed, _ = await asyncio.wait(
                (worker,),
                timeout=min(remaining_seconds, poll_seconds),
            )
            if worker in completed or worker.done():
                return True
            current_message_id = (
                await self._message_repo.get_latest_task_message_id_async(
                    task_id=task.task_id,
                    instance_id=instance_id,
                )
            )
            if current_message_id <= latest_message_id:
                continue
            latest_message_id = current_message_id
            previous_deadline = deadline
            deadline = max(deadline, time.monotonic() + timeout_seconds)
            if deadline > previous_deadline:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="task.execution.timeout_extended",
                    message="Task timeout extended after persisted progress",
                    payload={
                        "task_id": task.task_id,
                        "instance_id": instance_id,
                        "role_id": role_id,
                        "timeout_seconds": timeout_seconds,
                        "latest_message_id": current_message_id,
                    },
                )

    # ── Private helpers ─────────────────────────────────────────────────

    async def _maybe_enqueue_retry(self, task: TaskEnvelope) -> None:
        lifecycle = task.lifecycle
        if lifecycle.on_timeout != TaskTimeoutAction.RETRY:
            return
        next_attempt = task.retry_attempt + 1
        if next_attempt > lifecycle.max_retry_attempts:
            return
        await self.enqueue_retry_wakeup(task, next_attempt)

    async def _should_stop_heartbeat_after_skip(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
    ) -> bool:
        """Check whether heartbeat should stop after a skipped update.

        Migrated from TaskExecutionService._should_stop_heartbeat_after_skip
        L814-880.
        """
        try:
            record = await self._task_repo.get_async(task.task_id)
        except KeyError:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="task.execution.heartbeat_skipped",
                message="Task heartbeat stopped because task no longer exists",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                },
            )
            return True
        if record.status in {TaskStatus.CREATED, TaskStatus.ASSIGNED}:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="task.execution.heartbeat_waiting",
                message="Task heartbeat waiting for task to enter running state",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "status": record.status.value,
                },
            )
            return False
        if record.status == TaskStatus.RUNNING and record.assigned_instance_id in {
            None,
            instance_id,
        }:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="task.execution.heartbeat_waiting",
                message="Task heartbeat waiting after a transient running update miss",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "assigned_instance_id": record.assigned_instance_id or "",
                },
            )
            return False
        log_event(
            LOGGER,
            logging.DEBUG,
            event="task.execution.heartbeat_skipped",
            message="Task heartbeat stopped because task is no longer running here",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "status": record.status.value,
                "assigned_instance_id": record.assigned_instance_id or "",
            },
        )
        return True

    async def _mark_runtime_after_terminal_task_update_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        status: RunRuntimeStatus,
        phase: RunRuntimePhase,
        active_instance_id: str | None,
        active_task_id: str | None,
        active_role_id: str | None,
        active_subagent_instance_id: str | None,
        last_error: str | None,
    ) -> None:
        """Delegate to TaskPersistenceHarness for runtime state update."""
        await self._persistence_harness_async().mark_runtime_after_terminal_task_update_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            status=status,
            phase=phase,
            active_instance_id=active_instance_id,
            active_task_id=active_task_id,
            active_role_id=active_role_id,
            active_subagent_instance_id=active_subagent_instance_id,
            last_error=last_error,
        )

    def _persistence_harness_async(self) -> TaskPersistenceHarness:
        """Construct a TaskPersistenceHarness from current state."""
        return TaskPersistenceHarness.model_construct(
            task_repo=self._task_repo,
            run_runtime_repo=self._run_runtime_repo,
            run_control_manager=self._run_control_manager,
        )


# ── Module-level timeout helpers ───────────────────────────────────────


def _timeout_task_status(action: TaskTimeoutAction) -> TaskStatus:
    if action == TaskTimeoutAction.FAIL:
        return TaskStatus.TIMEOUT
    return TaskStatus.STOPPED


def _timeout_instance_status(action: TaskTimeoutAction) -> InstanceStatus:
    if action == TaskTimeoutAction.FAIL:
        return InstanceStatus.FAILED
    return InstanceStatus.IDLE


def _timeout_runtime_status(action: TaskTimeoutAction) -> RunRuntimeStatus:
    if action == TaskTimeoutAction.FAIL:
        return RunRuntimeStatus.RUNNING
    return RunRuntimeStatus.PAUSED


def _timeout_runtime_phase(action: TaskTimeoutAction) -> RunRuntimePhase:
    if action == TaskTimeoutAction.HUMAN_GATE:
        return RunRuntimePhase.AWAITING_MANUAL_ACTION
    if action == TaskTimeoutAction.RETRY:
        return RunRuntimePhase.AWAITING_RECOVERY
    return RunRuntimePhase.IDLE


def _timeout_progress_poll_seconds(timeout_seconds: float) -> float:
    return min(
        TASK_TIMEOUT_PROGRESS_POLL_MAX_SECONDS,
        max(TASK_TIMEOUT_PROGRESS_POLL_MIN_SECONDS, timeout_seconds / 10.0),
    )


def _timeout_handoff(*, task: TaskEnvelope, timeout_seconds: float) -> TaskHandoff:
    """Build a timeout handoff from task state.

    Merged from two prior implementations; uses the complete version from
    TaskExecutionService that preserves task.handoff.model_copy() logic.
    """
    if task.handoff is not None:
        reason = task.handoff.reason or f"timeout after {timeout_seconds:g}s"
        return task.handoff.model_copy(
            update={"reason": reason, "updated_at": datetime.now(tz=timezone.utc)}
        )
    return TaskHandoff(
        incomplete=(task.objective,),
        next_steps=(
            "Review the task conversation and tool history"
            " before retrying or splitting the task.",
        ),
        reason=f"timeout after {timeout_seconds:g}s",
    )
