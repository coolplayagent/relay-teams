# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.system_prompts import (
    RuntimePromptBuilder,
)
from relay_teams.agent_runtimes.instances.enums import InstanceStatus
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.orchestration.harnesses import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    ExecutionHarness,
    TaskLlmHarness,
    TaskPersistenceHarness,
    TaskPromptHarness,
    truncate_task_memory_result,
)
from relay_teams.agents.orchestration.harnesses.control_harness import (
    TaskControlHarness,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.orchestration.wakeup_auto_enqueue import (
    enqueue_blocker_resolved_wakeups,
    enqueue_dependency_wakeups,
)
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import (
    TaskArtifactPhase,
    TaskStatus,
    TaskTimeoutAction,
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
from relay_teams.hooks import HookService
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import MediaAssetService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders.service import SystemReminderService
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    RunCompletionReason,
    build_assistant_error_message,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RunEvent,
    RunThinkingConfig,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.tools.runtime.guardrails import generate_runtime_guardrail_report_async
from relay_teams.workspace import WorkspaceManager

LOGGER = get_logger(__name__)
TaskResultT = TypeVar("TaskResultT")
TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 5.0
TASK_TIMEOUT_PROGRESS_POLL_MAX_SECONDS = 1.0
TASK_TIMEOUT_PROGRESS_POLL_MIN_SECONDS = 0.001

__all__ = [
    "TASK_MEMORY_RESULT_EXCERPT_CHARS",
    "TaskExecutionService",
    "truncate_task_memory_result",
]


class TaskExecutionService(BaseModel):
    """Control plane for task execution.

    Orchestrates lifecycle transitions, timeout management, error handling,
    and state coordination.  Delegates compute operations to ExecutionHarness.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    task_repo: TaskRepository
    shared_store: SharedStateRepository
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    message_repo: MessageRepository
    approval_ticket_repo: ApprovalTicketRepository
    run_runtime_repo: RunRuntimeRepository
    run_event_hub: RunEventHub | None = None
    workspace_manager: WorkspaceManager
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition, str | None], object]
    tool_registry: object
    skill_registry: object
    skill_runtime_service: object | None = None
    mcp_registry: McpRegistry
    injection_manager: RunInjectionManager | None = None
    run_control_manager: RunControlManager | None = None
    role_memory_service: RoleMemoryService | None = None
    memory_event_handler: MemoryEventHandler | None = None
    runtime_role_resolver: RuntimeRoleResolver | None = None
    run_intent_repo: RunIntentRepository | None = None
    media_asset_service: MediaAssetService | None = None
    hook_service: HookService | None = None
    todo_service: TodoService | None = None
    reminder_service: SystemReminderService | None = None
    wakeup_repo: AgentWakeupRepository | None = None
    artifact_repo: TaskArtifactRepository | None = None

    # -- Harness factory -----------------------------------------------

    def _execution_harness(self) -> ExecutionHarness:
        return ExecutionHarness.model_construct(
            role_registry=getattr(self, "role_registry", None),
            task_repo=getattr(self, "task_repo", None),
            shared_store=getattr(self, "shared_store", None),
            event_bus=getattr(self, "event_bus", None),
            agent_repo=getattr(self, "agent_repo", None),
            message_repo=getattr(self, "message_repo", None),
            approval_ticket_repo=getattr(self, "approval_ticket_repo", None),
            run_runtime_repo=getattr(self, "run_runtime_repo", None),
            run_event_hub=getattr(self, "run_event_hub", None),
            workspace_manager=getattr(self, "workspace_manager", None),
            prompt_builder=getattr(self, "prompt_builder", None),
            provider_factory=getattr(self, "provider_factory", None),
            tool_registry=getattr(self, "tool_registry", None),
            skill_registry=getattr(self, "skill_registry", None),
            skill_runtime_service=getattr(self, "skill_runtime_service", None),
            mcp_registry=getattr(self, "mcp_registry", None),
            run_control_manager=getattr(self, "run_control_manager", None),
            role_memory_service=getattr(self, "role_memory_service", None),
            memory_event_handler=getattr(self, "memory_event_handler", None),
            run_intent_repo=getattr(self, "run_intent_repo", None),
            media_asset_service=getattr(self, "media_asset_service", None),
            hook_service=getattr(self, "hook_service", None),
            todo_service=getattr(self, "todo_service", None),
            reminder_service=getattr(self, "reminder_service", None),
            artifact_repo=getattr(self, "artifact_repo", None),
            runtime_role_resolver=getattr(self, "runtime_role_resolver", None),
        )

    def _control_harness(self) -> TaskControlHarness:
        """Construct a TaskControlHarness from current service state."""
        return TaskControlHarness(
            task_repo=self.task_repo,
            agent_repo=self.agent_repo,
            run_runtime_repo=self.run_runtime_repo,
            event_bus=self.event_bus,
            wakeup_repo=getattr(self, "wakeup_repo", None),
            artifact_repo=getattr(self, "artifact_repo", None),
        )

    # -- Public entry point --------------------------------------------

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> TaskExecutionResult:
        timeout_cancellation = asyncio.Event()
        worker = asyncio.create_task(
            self._execute_inner(
                instance_id=instance_id,
                role_id=role_id,
                task=task,
                user_prompt_override=user_prompt_override,
                timeout_cancellation=timeout_cancellation,
            )
        )
        heartbeat = self._start_task_heartbeat(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            worker=worker,
        )
        if self.run_control_manager is not None:
            self.run_control_manager.register_instance_task(
                run_id=task.trace_id,
                session_id=task.session_id,
                instance_id=instance_id,
                role_id=role_id,
                task_id=task.task_id,
                task=worker,
            )
        try:
            timeout_seconds = task.lifecycle.timeout_seconds
            if timeout_seconds is None:
                return await worker
            completed = await self._wait_for_worker_with_progress_timeout_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                worker=worker,
                timeout_seconds=timeout_seconds,
            )
            if completed:
                return await worker
            timeout_finalizer = asyncio.create_task(
                self._complete_timeout_after_worker_cancel_async(
                    task=task,
                    instance_id=instance_id,
                    role_id=role_id,
                    timeout_seconds=timeout_seconds,
                    worker=worker,
                    timeout_cancellation=timeout_cancellation,
                )
            )
            try:
                return await asyncio.shield(timeout_finalizer)
            except asyncio.CancelledError:
                _ = await asyncio.shield(timeout_finalizer)
                raise
        except asyncio.CancelledError:
            _ = await _cancel_and_wait(
                worker,
                suppress_exceptions=True,
                task_name="task_worker",
                context={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
            )
            raise
        finally:
            if heartbeat is not None:
                _ = await _cancel_and_wait(
                    heartbeat,
                    suppress_exceptions=True,
                    task_name="task_heartbeat",
                    context={
                        "task_id": task.task_id,
                        "instance_id": instance_id,
                        "role_id": role_id,
                    },
                )
            if self.run_control_manager is not None:
                self.run_control_manager.unregister_instance_task(
                    run_id=task.trace_id,
                    instance_id=instance_id,
                )

    # -- Core execution flow -------------------------------------------

    async def _wait_for_worker_with_progress_timeout_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        worker: asyncio.Task[TaskExecutionResult],
        timeout_seconds: float,
    ) -> bool:
        latest_message_id = await self.message_repo.get_latest_task_message_id_async(
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
                await self.message_repo.get_latest_task_message_id_async(
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

    async def _execute_inner(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None,
        timeout_cancellation: asyncio.Event,
    ) -> TaskExecutionResult:
        is_coordinator = self.role_registry.is_coordinator_role(role_id)
        log_event(
            LOGGER,
            logging.DEBUG,
            event="task.execution.started",
            message="Task execution started",
            payload={
                "task_id": task.task_id,
                "instance_id": instance_id,
                "role_id": role_id,
            },
        )

        # Control: initial state transitions
        await self.agent_repo.mark_status_async(instance_id, InstanceStatus.RUNNING)
        _ = await self.task_repo.update_status_async(
            task.task_id,
            TaskStatus.RUNNING,
            assigned_instance_id=instance_id,
        )
        await self.run_runtime_repo.ensure_async(
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
        await self.run_runtime_repo.update_async(
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
        await self.event_bus.emit_async(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )

        # OP-3: Create artifact container (spec phase).
        if self.artifact_repo is not None:
            try:
                await self.artifact_repo.ensure_artifact_async(
                    task_id=task.task_id,
                    spec_artifact_id=task.spec_artifact_id or "",
                )
                await self.artifact_repo.append_entry_async(
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

        # Compute: resolve workspace before try for error-path access
        harness = self._execution_harness()
        instance_record = await self.agent_repo.get_instance_async(instance_id)
        workspace = await self.workspace_manager.resolve_async(
            session_id=task.session_id,
            role_id=role_id,
            instance_id=instance_id,
            workspace_id=instance_record.workspace_id,
            conversation_id=instance_record.conversation_id,
        )

        try:
            # OP-3: Append implementation phase entry.
            if self.artifact_repo is not None:
                try:
                    await self.artifact_repo.append_entry_async(
                        task_id=task.task_id,
                        entry=TaskArtifactEntry(
                            entry_id=f"impl-{task.task_id}",
                            phase=TaskArtifactPhase.EXECUTION,
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                            role_id=role_id,
                            instance_id=instance_id,
                            event_type="llm_execution_start",
                            description="LLM execution started",
                            payload_json="{}",
                        ),
                    )
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="artifact.append_failed",
                        message="Failed to append implementation entry",
                        payload={"task_id": task.task_id, "error": str(exc)},
                    )

            # Compute: full execution config (role, runner, prompts, snapshot)
            config = await harness.prepare_execution_config(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                user_prompt_override=user_prompt_override,
                workspace=workspace,
                instance_record=instance_record,
            )

            # Compute: LLM execution
            guarded_result = await harness.run_llm_execution(
                config,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            _raise_if_timeout_cancellation_requested(
                timeout_cancellation,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            if isinstance(guarded_result, TaskExecutionResult):
                return guarded_result
            result = guarded_result

            # Compute: result handling
            await harness.handle_execution_result(
                result=result,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                workspace=workspace,
                instance_record=instance_record,
            )

            # OP-1: Enqueue dependency-resolved wakeups for downstream tasks
            if self.wakeup_repo is not None and self.task_repo is not None:
                try:
                    await enqueue_dependency_wakeups(
                        completed_task_id=task.task_id,
                        completed_task_envelope=task,
                        task_repo=self.task_repo,
                        wakeup_repo=self.wakeup_repo,
                    )
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="wakeup.dependency.enqueue_error",
                        message="Failed to enqueue dependency wakeups after task completion",
                        payload={"task_id": task.task_id, "error": str(exc)},
                    )

                # OP-2: Enqueue blocker-resolved wakeups for blocked tasks
                try:
                    await enqueue_blocker_resolved_wakeups(
                        completed_task_id=task.task_id,
                        completed_task_envelope=task,
                        task_repo=self.task_repo,
                        wakeup_repo=self.wakeup_repo,
                    )
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="wakeup.blocker.enqueue_error",
                        message="Failed to enqueue blocker-resolved wakeups after task completion",
                        payload={"task_id": task.task_id, "error": str(exc)},
                    )

            # Control: guardrail report
            await self._publish_runtime_guardrail_report_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )

            # OP-3: Append verification phase entry.
            if self.artifact_repo is not None:
                try:
                    await self.artifact_repo.append_entry_async(
                        task_id=task.task_id,
                        entry=TaskArtifactEntry(
                            entry_id=f"verify-{task.task_id}",
                            phase=TaskArtifactPhase.VERIFICATION,
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                            role_id=role_id,
                            instance_id=instance_id,
                            event_type="guardrail_report_completed",
                            description="Guardrail report and runtime checks completed",
                            payload_json="{}",
                        ),
                    )
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="artifact.verify_failed",
                        message="Failed to append verification entry",
                        payload={"task_id": task.task_id, "error": str(exc)},
                    )

            # OP-3: Append delivery phase entry and update summary.
            if self.artifact_repo is not None:
                try:
                    await self.artifact_repo.append_entry_async(
                        task_id=task.task_id,
                        entry=TaskArtifactEntry(
                            entry_id=f"delivery-{task.task_id}",
                            phase=TaskArtifactPhase.DELIVERY,
                            timestamp=datetime.now(tz=timezone.utc).isoformat(),
                            role_id=role_id,
                            instance_id=instance_id,
                            event_type="task_completed",
                            description="Task execution completed",
                            payload_json="{}",
                        ),
                    )
                    await self.artifact_repo.update_summary_async(
                        task_id=task.task_id,
                        summary=result,
                    )
                except Exception as exc:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        event="artifact.delivery_failed",
                        message="Failed to append delivery entry",
                        payload={"task_id": task.task_id, "error": str(exc)},
                    )
            return TaskExecutionResult(output=result)
        except asyncio.CancelledError:
            if timeout_cancellation.is_set():
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="task.execution.timeout_cancelled",
                    message="Task worker cancelled for lifecycle timeout",
                    payload={
                        "task_id": task.task_id,
                        "instance_id": instance_id,
                        "role_id": role_id,
                    },
                )
                raise
            cleanup_task = asyncio.create_task(
                self._persist_cancelled_execution_async(
                    task=task,
                    instance_id=instance_id,
                    role_id=role_id,
                    is_coordinator=is_coordinator,
                )
            )
            stopped, paused_subagent = await _await_cancellation_resistant_task(
                cleanup_task
            )
            log_event(
                LOGGER,
                logging.WARNING if stopped else logging.ERROR,
                event="task.execution.stopped"
                if stopped
                else "task.execution.cancelled",
                message="Task execution interrupted",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "paused_subagent": paused_subagent,
                },
            )
            raise
        except AssistantRunError as exc:
            _raise_if_timeout_cancellation_requested(
                timeout_cancellation,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            return await harness.complete_with_assistant_error_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=exc.payload.assistant_message,
                error_code=exc.payload.error_code,
                error_message=exc.payload.error_message,
                append_message=False,
            )
        except RecoverableRunPauseError as exc:
            _raise_if_timeout_cancellation_requested(
                timeout_cancellation,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            payload = exc.payload
            _ = await self.task_repo.update_status_async(
                task.task_id,
                TaskStatus.STOPPED,
                assigned_instance_id=instance_id,
                result=payload.assistant_message or None,
                error_message=payload.error_message,
            )
            await self.agent_repo.mark_status_async(instance_id, InstanceStatus.IDLE)
            await self.run_runtime_repo.update_async(
                task.trace_id,
                status=RunRuntimeStatus.PAUSED,
                phase=payload.runtime_phase or RunRuntimePhase.AWAITING_RECOVERY,
                active_instance_id=payload.instance_id,
                active_task_id=payload.task_id,
                active_role_id=payload.role_id,
                active_subagent_instance_id=(
                    None if is_coordinator else payload.instance_id
                ),
                last_error=payload.error_message,
            )
            await self.event_bus.emit_async(
                EventEnvelope(
                    event_type=EventType.TASK_STOPPED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            log_event(
                LOGGER,
                logging.WARNING,
                event="task.execution.paused",
                message="Task execution paused after recoverable model interruption",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                    "error_code": payload.error_code,
                },
            )
            raise
        except TimeoutError:
            _raise_if_timeout_cancellation_requested(
                timeout_cancellation,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            return await harness.complete_with_assistant_error_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=build_assistant_error_message(
                    error_code="task_timeout",
                    error_message="Task timeout",
                ),
                error_code="task_timeout",
                error_message="Task timeout",
            )
        except Exception as exc:
            _raise_if_timeout_cancellation_requested(
                timeout_cancellation,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )
            return await harness.complete_with_assistant_error_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=workspace.ref.conversation_id,
                workspace_id=workspace.ref.workspace_id,
                assistant_message=build_assistant_error_message(
                    error_code="internal_execution_error",
                    error_message=str(exc),
                ),
                error_code="internal_execution_error",
                error_message=str(exc),
            )

    # -- Guardrail report ----------------------------------------------

    async def _publish_runtime_guardrail_report_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
    ) -> None:
        try:
            report = await generate_runtime_guardrail_report_async(
                shared_store=self.shared_store,
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
            if self.run_event_hub is not None:
                _ = await self.run_event_hub.publish_async(event)
            else:
                _ = await self.event_bus.emit_run_event_async(event)
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

    # -- Heartbeat -----------------------------------------------------

    def _start_task_heartbeat(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        worker: asyncio.Task[TaskExecutionResult],
    ) -> asyncio.Task[None] | None:
        interval = task.lifecycle.heartbeat_interval_seconds
        if interval is None:
            return None
        return asyncio.create_task(
            self._heartbeat_task_until_done(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                interval_seconds=interval,
                worker=worker,
            )
        )

    async def _heartbeat_task_until_done(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        interval_seconds: float,
        worker: asyncio.Task[TaskExecutionResult],
    ) -> None:
        while not worker.done():
            await asyncio.sleep(interval_seconds)
            if worker.done():
                return
            updated = await self.task_repo.heartbeat_running_async(
                task.task_id,
                assigned_instance_id=instance_id,
            )
            if not updated:
                should_stop = await self._should_stop_heartbeat_after_skip(
                    task=task,
                    instance_id=instance_id,
                    role_id=role_id,
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
                    "role_id": role_id,
                },
            )

    async def _should_stop_heartbeat_after_skip(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
    ) -> bool:
        try:
            record = await self.task_repo.get_async(task.task_id)
        except KeyError:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="task.execution.heartbeat_skipped",
                message="Task heartbeat stopped because task no longer exists",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
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
                    "role_id": role_id,
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
                    "role_id": role_id,
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
                "role_id": role_id,
                "status": record.status.value,
                "assigned_instance_id": record.assigned_instance_id or "",
            },
        )
        return True

    # -- Timeout management --------------------------------------------

    async def _complete_task_timeout_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        timeout_seconds: float,
    ) -> TaskExecutionResult:
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
        current = await self.task_repo.get_async(task.task_id)
        handoff = _timeout_handoff(
            task=current.envelope, timeout_seconds=timeout_seconds
        )
        await self.task_repo.update_envelope_async(
            task.task_id,
            current.envelope.model_copy(update={"handoff": handoff}),
        )
        await self.task_repo.update_status_async(
            task.task_id,
            task_status,
            assigned_instance_id=instance_id,
            result=assistant_message,
            error_message=error_message,
        )
        await self.agent_repo.mark_status_async(instance_id, instance_status)
        await self.run_runtime_repo.ensure_async(
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
        await self.event_bus.emit_async(
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
            if retry_attempt < max_attempts and self.wakeup_repo is not None:
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
                await self.wakeup_repo.enqueue_async(entry)
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

    async def _complete_timeout_after_worker_cancel_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        timeout_seconds: float,
        worker: asyncio.Task[TaskExecutionResult],
        timeout_cancellation: asyncio.Event,
    ) -> TaskExecutionResult:
        timeout_cancellation.set()
        cancel_result = await _cancel_and_wait(
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
            return cancel_result
        return await self._complete_task_timeout_async(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            timeout_seconds=timeout_seconds,
        )

    # -- Cancelled execution persistence -------------------------------

    async def _persist_cancelled_execution_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        is_coordinator: bool,
    ) -> tuple[bool, bool]:
        paused_subagent = False
        if self.run_control_manager is not None:
            run_stop_requested = self.run_control_manager.is_run_stop_requested(
                task.trace_id
            )
            subagent_stop_requested = (
                self.run_control_manager.is_subagent_stop_requested(
                    run_id=task.trace_id,
                    instance_id=instance_id,
                )
            )
            stopped = await self.run_control_manager.handle_instance_cancelled_async(
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
            await self.task_repo.update_status_async(
                task.task_id,
                TaskStatus.FAILED,
                error_message="Task cancelled",
            )
            await self.agent_repo.mark_status_async(instance_id, InstanceStatus.FAILED)
            await self.event_bus.emit_async(
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
            if not await self._promote_running_runtime_lane_async(
                run_id=task.trace_id,
                terminal_task_id=task.task_id,
                last_error=last_error,
            ):
                await self.run_runtime_repo.update_async(
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
                status=RunRuntimeStatus.STOPPED if stopped else RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=last_error,
            )
        return stopped, paused_subagent

    # -- Thin wrappers preserving async-wrapper coverage ----------------

    async def _thinking_for_run_async(self, run_id: str) -> RunThinkingConfig:
        return await TaskLlmHarness.model_construct(
            run_intent_repo=getattr(self, "run_intent_repo", None),
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).thinking_for_run_async(run_id)

    async def _execute_task_completed_hooks(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        output_text: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            run_event_hub=getattr(self, "run_event_hub", None),
            hook_service=getattr(self, "hook_service", None),
        ).execute_task_completed_hooks(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            output_text=output_text,
        )

    async def _mark_runtime_idle_after_success_async(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            task_repo=getattr(self, "task_repo", None),
            run_runtime_repo=getattr(self, "run_runtime_repo", None),
            run_control_manager=getattr(self, "run_control_manager", None),
        ).mark_runtime_idle_after_success_async(
            run_id=run_id,
            completed_task_id=completed_task_id,
        )

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
        await TaskPersistenceHarness.model_construct(
            task_repo=getattr(self, "task_repo", None),
            run_runtime_repo=getattr(self, "run_runtime_repo", None),
            run_control_manager=getattr(self, "run_control_manager", None),
        ).mark_runtime_after_terminal_task_update_async(
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

    async def _promote_running_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: str | None,
    ) -> bool:
        return await TaskPersistenceHarness.model_construct(
            task_repo=getattr(self, "task_repo", None),
            run_runtime_repo=getattr(self, "run_runtime_repo", None),
            run_control_manager=getattr(self, "run_control_manager", None),
        ).promote_running_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    async def _ensure_committed_task_prompt_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        conversation_id: str,
        instance_id: str,
        task: TaskEnvelope,
        user_prompt_text: str,
        user_prompt_override: str | None,
    ) -> None:
        await TaskPromptHarness.model_construct(
            message_repo=getattr(self, "message_repo", None),
            run_intent_repo=getattr(self, "run_intent_repo", None),
            media_asset_service=getattr(self, "media_asset_service", None),
        ).ensure_committed_task_prompt_async(
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=user_prompt_text,
            user_prompt_override=user_prompt_override,
        )


async def _cancel_and_wait(
    task: asyncio.Task[TaskResultT],
    *,
    suppress_exceptions: bool = False,
    task_name: str = "task",
    timeout_seconds: float | None = None,
    context: Mapping[str, JsonValue] | None = None,
) -> TaskResultT | None:
    task.cancel()
    if timeout_seconds is not None:
        completed, _ = await asyncio.wait((task,), timeout=timeout_seconds)
        if task not in completed:
            task.add_done_callback(
                lambda t: _consume_cancelled_background_result(
                    t,
                    task_name=task_name,
                    context=context,
                )
            )
            log_event(
                LOGGER,
                logging.WARNING,
                event="task.execution.background_cancel_timeout",
                message="Background task did not stop before cancel wait timeout",
                payload={
                    "task_name": task_name,
                    "timeout_seconds": timeout_seconds,
                    **dict(context or {}),
                },
            )
            return None
        try:
            return task.result()
        except asyncio.CancelledError:
            return None
        except Exception as exc:
            if not suppress_exceptions:
                raise
            log_event(
                LOGGER,
                logging.WARNING,
                event="task.execution.background_cancel_failed",
                message="Background task raised while being cancelled",
                payload={
                    "task_name": task_name,
                    "error": str(exc),
                    **dict(context or {}),
                },
            )
            return None
    try:
        return await task
    except asyncio.CancelledError:
        return None
    except Exception as exc:
        if not suppress_exceptions:
            raise
        log_event(
            LOGGER,
            logging.WARNING,
            event="task.execution.background_cancel_failed",
            message="Background task raised while being cancelled",
            payload={"task_name": task_name, "error": str(exc), **dict(context or {})},
        )
        return None


async def _await_cancellation_resistant_task(
    task: asyncio.Task[TaskResultT],
) -> TaskResultT:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            _clear_current_task_cancellation_requests()
    return task.result()


def _clear_current_task_cancellation_requests() -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return
    while current_task.cancelling():
        current_task.uncancel()


def _raise_if_timeout_cancellation_requested(
    timeout_cancellation: asyncio.Event,
    *,
    task: TaskEnvelope,
    instance_id: str,
    role_id: str,
) -> None:
    if not timeout_cancellation.is_set():
        return
    log_event(
        LOGGER,
        logging.DEBUG,
        event="task.execution.timeout_cancelled",
        message="Task worker result ignored after lifecycle timeout",
        payload={
            "task_id": task.task_id,
            "instance_id": instance_id,
            "role_id": role_id,
        },
    )
    raise asyncio.CancelledError


def _consume_cancelled_background_result(
    task: asyncio.Task[TaskResultT],
    *,
    task_name: str,
    context: Mapping[str, JsonValue] | None,
) -> None:
    try:
        _ = task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="task.execution.background_cancel_failed",
            message="Background task raised after cancel wait timeout",
            payload={"task_name": task_name, "error": str(exc), **dict(context or {})},
        )


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
    if task.handoff is not None:
        reason = task.handoff.reason or f"timeout after {timeout_seconds:g}s"
        return task.handoff.model_copy(
            update={"reason": reason, "updated_at": datetime.now(tz=timezone.utc)}
        )
    return TaskHandoff(
        incomplete=(task.objective,),
        next_steps=(
            "Review the task conversation and tool history before retrying or splitting the task.",
        ),
        reason=f"timeout after {timeout_seconds:g}s",
    )
