# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.system_prompts import (
    RuntimePromptBuilder,
)
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
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
from relay_teams.agents.tasks.agent_wakeup_repository import AgentWakeupRepository
from relay_teams.agents.tasks.artifact_repository import TaskArtifactRepository
from relay_teams.agents.tasks.enums import (
    TaskArtifactPhase,
    TaskStatus,
)
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import (
    TaskArtifactEntry,
    TaskEnvelope,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import HookService
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import MediaAssetService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders import SystemReminderService
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.sessions.runs.assistant_errors import (
    AssistantRunError,
    build_assistant_error_message,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
)
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RunThinkingConfig,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceManager

LOGGER = get_logger(__name__)
TaskResultT = TypeVar("TaskResultT")
TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 5.0
TASK_TIMEOUT_PROGRESS_POLL_MAX_SECONDS = 1.0
TASK_TIMEOUT_PROGRESS_POLL_MIN_SECONDS = 0.001

__all__ = [
    "TASK_MEMORY_RESULT_EXCERPT_CHARS",
    "TaskExecutionService",
    "cancel_and_wait",
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

    # ── Harness factory ───────────────────────────────────────────────

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
            message_repo=getattr(self, "message_repo", None),
            shared_store=getattr(self, "shared_store", None),
            run_event_hub=getattr(self, "run_event_hub", None),
            run_control_manager=getattr(self, "run_control_manager", None),
        )

    # ── Public entry point ────────────────────────────────────────────

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
        heartbeat = self._control_harness().start_heartbeat(
            task=task,
            instance_id=instance_id,
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
            completed = (
                await self._control_harness().wait_for_worker_with_progress_timeout(
                    task=task,
                    instance_id=instance_id,
                    role_id=role_id,
                    worker=worker,
                    timeout_seconds=timeout_seconds,
                )
            )
            if completed:
                return await worker
            timeout_finalizer = asyncio.create_task(
                self._control_harness().complete_timeout_after_worker_cancel(
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
            _ = await cancel_and_wait(
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
                _ = await cancel_and_wait(
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

    # ── Core execution flow ───────────────────────────────────────────

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
        await self._control_harness().transition_task_to_running(
            task,
            instance_id,
            role_id,
            is_coordinator,
        )

        # OP-3: Create artifact container (spec phase).
        self._control_harness().initialize_task_artifact(
            task,
            instance_id,
            role_id,
        )

        # Compute: resolve workspace before try for error-path access
        harness = self._execution_harness()
        instance_record = await self.agent_repo.get_instance_async(instance_id)
        workspace = self.workspace_manager.resolve(
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
                    self.artifact_repo.append_entry(
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

            # Control: guardrail report
            await self._control_harness().publish_guardrail_report(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
            )

            # OP-3: Append verification phase entry.
            if self.artifact_repo is not None:
                try:
                    self.artifact_repo.append_entry(
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
                    self.artifact_repo.append_entry(
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
                    self.artifact_repo.update_summary(
                        task_id=task.task_id, summary=result
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
                self._control_harness().persist_cancelled_execution(
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

    # ── Thin wrappers preserving async-wrapper coverage ────────────────

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


async def cancel_and_wait(
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
