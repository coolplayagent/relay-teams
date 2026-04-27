# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.execution.subagent_runner import SubAgentRunner
from relay_teams.agents.execution.system_prompts import (
    PromptSkillInstruction,
    RuntimePromptBuilder,
    RuntimePromptSections,
)
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import (
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.agents.orchestration.harnesses import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    PreparedRuntimeSnapshot,
    TaskLlmHarness,
    TaskPersistenceHarness,
    TaskPromptHarness,
    TaskToolHarness,
    truncate_task_memory_result,
)
from relay_teams.agents.orchestration.harnesses.prompt_harness import (
    ProviderUserPromptContent,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.agents.tasks.models import TaskEnvelope, TaskHandoff
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import HookService
from relay_teams.logger import get_logger, log_event
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.media import MediaAssetService
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders import ReminderDecision, SystemReminderService
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
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import (
    RunKind,
    RuntimePromptConversationContext,
    RunThinkingConfig,
    RunTopologySnapshot,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.skills.skill_models import SkillInstructionEntry
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceHandle, WorkspaceManager

LOGGER = get_logger(__name__)
TaskResultT = TypeVar("TaskResultT")
TIMEOUT_WORKER_CANCEL_GRACE_SECONDS = 5.0
__all__ = [
    "TASK_MEMORY_RESULT_EXCERPT_CHARS",
    "TaskExecutionService",
    "truncate_task_memory_result",
]


class TaskExecutionService(BaseModel):
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
    runtime_role_resolver: RuntimeRoleResolver | None = None
    run_intent_repo: RunIntentRepository | None = None
    media_asset_service: MediaAssetService | None = None
    hook_service: HookService | None = None
    todo_service: TodoService | None = None
    reminder_service: SystemReminderService | None = None

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
            completed, _ = await asyncio.wait((worker,), timeout=timeout_seconds)
            if worker in completed:
                return await worker
            if worker.done():
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

        if self.runtime_role_resolver is not None:
            role = await self.runtime_role_resolver.get_effective_role_async(
                run_id=task.trace_id,
                role_id=role_id,
            )
        else:
            role = self.role_registry.get(role_id)
        instance_record = await self.agent_repo.get_instance_async(instance_id)
        workspace = self.workspace_manager.resolve(
            session_id=task.session_id,
            role_id=role_id,
            instance_id=instance_id,
            workspace_id=instance_record.workspace_id,
            conversation_id=instance_record.conversation_id,
        )
        role_for_run = self._role_with_memory(
            role=role,
            role_id=role_id,
            workspace_id=workspace.ref.workspace_id,
        )
        session_mode = "normal"
        run_kind = RunKind.CONVERSATION
        if self.run_intent_repo is not None:
            try:
                intent = self.run_intent_repo.get(
                    task.trace_id,
                    fallback_session_id=task.session_id,
                )
                session_mode = intent.session_mode.value
                run_kind = intent.run_kind
            except KeyError:
                # Some direct task-execution tests and legacy flows have no run intent.
                pass
        runner = SubAgentRunner(
            role=role_for_run,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role_for_run, task.session_id),
            session_mode=session_mode,
            run_kind=run_kind,
        )
        snapshot = await self._shared_state_snapshot_async(
            session_id=task.session_id,
            role_id=role_id,
            conversation_id=workspace.ref.conversation_id,
        )
        try:
            prepared_runtime_snapshot = await self._prepare_runtime_snapshot(
                role=role_for_run,
                task=task,
                working_directory=workspace.resolve_workdir(),
                worktree_root=workspace.scope_root,
                workspace=workspace,
                shared_state_snapshot=snapshot,
                objective=self._resolve_turn_objective(
                    task=task,
                    user_prompt_override=user_prompt_override,
                ),
            )
            await self._ensure_committed_task_prompt_async(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
                instance_id=instance_id,
                task=task,
                user_prompt_text=prepared_runtime_snapshot.user_prompt,
                user_prompt_override=user_prompt_override,
            )
            runtime_prompt_sections = prepared_runtime_snapshot.prompt_sections
            runtime_tools_json = prepared_runtime_snapshot.runtime_tools_json
            runtime_system_prompt = self._compose_runtime_system_prompt(
                runtime_prompt_sections=runtime_prompt_sections,
                skill_instructions=prepared_runtime_snapshot.skill_instructions,
            )
            await self.agent_repo.update_runtime_snapshot_async(
                instance_id,
                runtime_system_prompt=runtime_system_prompt,
                runtime_tools_json=runtime_tools_json,
            )
            provider_system_prompt = self._compose_provider_system_prompt(
                runtime_prompt_sections=runtime_prompt_sections,
                skill_instructions=prepared_runtime_snapshot.skill_instructions,
            )
            guarded_result = await self._run_with_completion_guard(
                runner=runner,
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                workspace=workspace,
                conversation_id=workspace.ref.conversation_id,
                shared_state_snapshot=snapshot,
                system_prompt_override=provider_system_prompt,
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
            await self._execute_task_completed_hooks(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                output_text=result,
            )
            await self.task_repo.update_status_async(
                task.task_id, TaskStatus.COMPLETED, result=result
            )
            await self.agent_repo.mark_status_async(
                instance_id, InstanceStatus.COMPLETED
            )
            await self._mark_runtime_idle_after_success_async(
                run_id=task.trace_id,
                completed_task_id=task.task_id,
            )
            await self.event_bus.emit_async(
                EventEnvelope(
                    event_type=EventType.TASK_COMPLETED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            await self._record_memory_if_needed_async(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                task=task,
                conversation_id=workspace.ref.conversation_id,
                instance_id=instance_id,
                lifecycle=instance_record.lifecycle.value,
                result=result,
            )
            log_event(
                LOGGER,
                logging.DEBUG,
                event="task.execution.completed",
                message="Task execution completed",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
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
                stopped = self.run_control_manager.handle_instance_cancelled(
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
                await self.agent_repo.mark_status_async(
                    instance_id, InstanceStatus.FAILED
                )
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
            return await self._complete_with_assistant_error_async(
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
            return await self._complete_with_assistant_error_async(
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
            return await self._complete_with_assistant_error_async(
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
            task=current.envelope,
            timeout_seconds=timeout_seconds,
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

    def _tool_harness(self) -> TaskToolHarness:
        return TaskToolHarness.model_construct(
            role_registry=self.role_registry,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
            mcp_registry=self.mcp_registry,
        )

    def _prompt_harness(self) -> TaskPromptHarness:
        return TaskPromptHarness.model_construct(
            role_registry=self.role_registry,
            shared_store=self.shared_store,
            message_repo=self.message_repo,
            workspace_manager=self.workspace_manager,
            prompt_builder=self.prompt_builder,
            tool_harness=self._tool_harness(),
            skill_runtime_service=self.skill_runtime_service,
            role_memory_service=self.role_memory_service,
            runtime_role_resolver=self.runtime_role_resolver,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        )

    def _full_persistence_harness(self) -> TaskPersistenceHarness:
        return TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            shared_store=self.shared_store,
            event_bus=self.event_bus,
            agent_repo=self.agent_repo,
            message_repo=self.message_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_event_hub=self.run_event_hub,
            run_control_manager=self.run_control_manager,
            hook_service=self.hook_service,
        )

    def _llm_harness(self) -> TaskLlmHarness:
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=self._full_persistence_harness(),
        )

    async def _run_with_completion_guard(
        self,
        *,
        runner: SubAgentRunner,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        system_prompt_override: str,
    ) -> str | TaskExecutionResult:
        return await self._llm_harness().run_with_completion_guard(
            runner=runner,
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            system_prompt_override=system_prompt_override,
        )

    async def _run_agent_once(
        self,
        *,
        runner: SubAgentRunner,
        task: TaskEnvelope,
        instance_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        system_prompt_override: str,
    ) -> str:
        return await self._llm_harness().run_agent_once(
            runner=runner,
            task=task,
            instance_id=instance_id,
            workspace=workspace,
            conversation_id=conversation_id,
            shared_state_snapshot=shared_state_snapshot,
            system_prompt_override=system_prompt_override,
        )

    async def _evaluate_completion_guard_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        output_text: str,
    ) -> ReminderDecision:
        return await TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).evaluate_completion_guard_async(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            output_text=output_text,
        )

    async def _thinking_for_run_async(self, run_id: str) -> RunThinkingConfig:
        return await TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).thinking_for_run_async(run_id)

    def _evaluate_completion_guard(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        workspace: WorkspaceHandle,
        conversation_id: str,
        output_text: str,
    ) -> ReminderDecision:
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            todo_service=self.todo_service,
            reminder_service=self.reminder_service,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).evaluate_completion_guard(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            workspace=workspace,
            conversation_id=conversation_id,
            output_text=output_text,
        )

    def _thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        return TaskLlmHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
            persistence_harness=TaskPersistenceHarness.model_construct(),
        ).thinking_for_run(run_id)

    async def _execute_task_completed_hooks(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        output_text: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            run_event_hub=self.run_event_hub,
            hook_service=self.hook_service,
        ).execute_task_completed_hooks(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            output_text=output_text,
        )

    def _complete_with_assistant_error(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        conversation_id: str,
        workspace_id: str,
        assistant_message: str,
        error_code: str,
        error_message: str,
        append_message: bool = True,
    ) -> TaskExecutionResult:
        return self._full_persistence_harness().complete_with_assistant_error(
            task=task,
            instance_id=instance_id,
            role_id=role_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            assistant_message=assistant_message,
            error_code=error_code,
            error_message=error_message,
            append_message=append_message,
        )

    async def _complete_with_assistant_error_async(
        self,
        *,
        task: TaskEnvelope,
        instance_id: str,
        role_id: str,
        conversation_id: str,
        workspace_id: str,
        assistant_message: str,
        error_code: str,
        error_message: str,
        append_message: bool = True,
    ) -> TaskExecutionResult:
        return (
            await self._full_persistence_harness().complete_with_assistant_error_async(
                task=task,
                instance_id=instance_id,
                role_id=role_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                assistant_message=assistant_message,
                error_code=error_code,
                error_message=error_message,
                append_message=append_message,
            )
        )

    def _topology_for_run(self, run_id: str) -> RunTopologySnapshot | None:
        return TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).topology_for_run(run_id)

    def _conversation_context_for_run(
        self,
        run_id: str,
    ) -> RuntimePromptConversationContext | None:
        return TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).conversation_context_for_run(run_id)

    async def _topology_for_run_async(self, run_id: str) -> RunTopologySnapshot | None:
        return await TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).topology_for_run_async(run_id)

    async def _conversation_context_for_run_async(
        self,
        run_id: str,
    ) -> RuntimePromptConversationContext | None:
        return await TaskPromptHarness.model_construct(
            run_intent_repo=self.run_intent_repo,
        ).conversation_context_for_run_async(run_id)

    def _role_with_memory(
        self,
        *,
        role: RoleDefinition,
        role_id: str,
        workspace_id: str,
    ) -> RoleDefinition:
        return TaskPromptHarness.model_construct(
            role_registry=self.role_registry,
            role_memory_service=self.role_memory_service,
        ).role_with_memory(
            role=role,
            role_id=role_id,
            workspace_id=workspace_id,
        )

    async def _prepare_runtime_snapshot(
        self,
        *,
        role: RoleDefinition,
        task: TaskEnvelope,
        working_directory: Path | None,
        worktree_root: Path | None,
        workspace: WorkspaceHandle | None,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        objective: str,
    ) -> PreparedRuntimeSnapshot:
        return await self._prompt_harness().prepare_runtime_snapshot(
            role=role,
            task=task,
            working_directory=working_directory,
            worktree_root=worktree_root,
            workspace=workspace,
            shared_state_snapshot=shared_state_snapshot,
            objective=objective,
        )

    @staticmethod
    def _compose_runtime_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return TaskPromptHarness.model_construct().compose_runtime_system_prompt(
            runtime_prompt_sections=runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    @staticmethod
    def _compose_provider_system_prompt(
        *,
        runtime_prompt_sections: RuntimePromptSections,
        skill_instructions: tuple[PromptSkillInstruction, ...],
    ) -> str:
        return TaskPromptHarness.model_construct().compose_provider_system_prompt(
            runtime_prompt_sections=runtime_prompt_sections,
            skill_instructions=skill_instructions,
        )

    async def _build_runtime_tools_snapshot(
        self,
        role: RoleDefinition,
        task: TaskEnvelope | None = None,
    ) -> RuntimeToolsSnapshot:
        return await self._tool_harness().build_runtime_tools_snapshot(
            role=role,
            task=task,
        )

    @staticmethod
    def _tool_entry_from_definition(
        *,
        source: Literal["local", "skill", "mcp"],
        name: str,
        description: str,
        kind: Literal["function", "output", "external", "unapproved"],
        strict: bool | None,
        sequential: bool,
        parameters_json_schema: Mapping[str, JsonValue],
        server_name: str = "",
    ) -> RuntimeToolSnapshotEntry:
        return TaskToolHarness.model_construct().tool_entry_from_definition(
            source=source,
            name=name,
            description=description,
            kind=kind,
            strict=strict,
            sequential=sequential,
            parameters_json_schema=parameters_json_schema,
            server_name=server_name,
        )

    @staticmethod
    def _normalize_tool_kind(
        kind: str,
    ) -> Literal["function", "output", "external", "unapproved"]:
        return TaskToolHarness.model_construct().normalize_tool_kind(kind)

    def _record_memory_if_needed(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        instance_id: str,
        lifecycle: str,
        result: str,
    ) -> None:
        TaskPersistenceHarness.model_construct(
            shared_store=self.shared_store,
        ).record_memory_if_needed(
            role_id=role_id,
            workspace_id=workspace_id,
            task=task,
            conversation_id=conversation_id,
            instance_id=instance_id,
            lifecycle=lifecycle,
            result=result,
        )

    async def _record_memory_if_needed_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        instance_id: str,
        lifecycle: str,
        result: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            shared_store=self.shared_store,
        ).record_memory_if_needed_async(
            role_id=role_id,
            workspace_id=workspace_id,
            task=task,
            conversation_id=conversation_id,
            instance_id=instance_id,
            lifecycle=lifecycle,
            result=result,
        )

    def _runtime_persistence_harness(self) -> TaskPersistenceHarness:
        return TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        )

    def _mark_runtime_idle_after_success(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        self._runtime_persistence_harness().mark_runtime_idle_after_success(
            run_id=run_id,
            completed_task_id=completed_task_id,
        )

    async def _mark_runtime_idle_after_success_async(
        self,
        *,
        run_id: str,
        completed_task_id: str,
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).mark_runtime_idle_after_success_async(
            run_id=run_id,
            completed_task_id=completed_task_id,
        )

    def _mark_runtime_after_terminal_task_update(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        status: RunRuntimeStatus,
        phase: RunRuntimePhase,
        active_instance_id: Optional[str],
        active_task_id: Optional[str],
        active_role_id: Optional[str],
        active_subagent_instance_id: Optional[str],
        last_error: Optional[str],
    ) -> None:
        self._runtime_persistence_harness().mark_runtime_after_terminal_task_update(
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

    async def _mark_runtime_after_terminal_task_update_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        status: RunRuntimeStatus,
        phase: RunRuntimePhase,
        active_instance_id: Optional[str],
        active_task_id: Optional[str],
        active_role_id: Optional[str],
        active_subagent_instance_id: Optional[str],
        last_error: Optional[str],
    ) -> None:
        await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
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

    def _promote_running_runtime_lane(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: Optional[str],
    ) -> bool:
        return self._runtime_persistence_harness().promote_running_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    async def _promote_running_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: Optional[str],
    ) -> bool:
        return await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).promote_running_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    def _promote_paused_runtime_lane(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: Optional[str],
    ) -> bool:
        return self._runtime_persistence_harness().promote_paused_runtime_lane(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    async def _promote_paused_runtime_lane_async(
        self,
        *,
        run_id: str,
        terminal_task_id: str,
        last_error: Optional[str],
    ) -> bool:
        return await TaskPersistenceHarness.model_construct(
            task_repo=self.task_repo,
            run_runtime_repo=self.run_runtime_repo,
            run_control_manager=self.run_control_manager,
        ).promote_paused_runtime_lane_async(
            run_id=run_id,
            terminal_task_id=terminal_task_id,
            last_error=last_error,
        )

    def _shared_state_snapshot(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return TaskPromptHarness.model_construct(
            shared_store=self.shared_store,
        ).shared_state_snapshot(
            session_id=session_id,
            role_id=role_id,
            conversation_id=conversation_id,
        )

    async def _shared_state_snapshot_async(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        return await TaskPromptHarness.model_construct(
            shared_store=self.shared_store,
        ).shared_state_snapshot_async(
            session_id=session_id,
            role_id=role_id,
            conversation_id=conversation_id,
        )

    def _ensure_committed_task_prompt(
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
        TaskPromptHarness.model_construct(
            message_repo=self.message_repo,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        ).ensure_committed_task_prompt(
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=user_prompt_text,
            user_prompt_override=user_prompt_override,
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
            message_repo=self.message_repo,
            run_intent_repo=self.run_intent_repo,
            media_asset_service=self.media_asset_service,
        ).ensure_committed_task_prompt_async(
            role_id=role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            instance_id=instance_id,
            task=task,
            user_prompt_text=user_prompt_text,
            user_prompt_override=user_prompt_override,
        )

    def _build_user_prompt(
        self,
        *,
        role: RoleDefinition,
        objective: str,
        shared_state_snapshot: tuple[tuple[str, str], ...],
        conversation_context: RuntimePromptConversationContext | None,
        orchestration_prompt: str,
        skill_names: Optional[tuple[str, ...]] = None,
    ) -> tuple[str, tuple[PromptSkillInstruction, ...]]:
        return TaskPromptHarness.model_construct(
            skill_runtime_service=self.skill_runtime_service,
        ).build_user_prompt(
            role=role,
            objective=objective,
            shared_state_snapshot=shared_state_snapshot,
            conversation_context=conversation_context,
            orchestration_prompt=orchestration_prompt,
            skill_names=skill_names,
        )

    @staticmethod
    def _to_prompt_skill_instructions(
        entries: tuple[SkillInstructionEntry, ...],
    ) -> tuple[PromptSkillInstruction, ...]:
        return TaskPromptHarness.model_construct().to_prompt_skill_instructions(entries)

    @staticmethod
    def _merge_provider_prompt_content(
        *,
        provider_content: ProviderUserPromptContent,
        user_prompt_text: str,
    ) -> ProviderUserPromptContent:
        return TaskPromptHarness.model_construct().merge_provider_prompt_content(
            provider_content=provider_content,
            user_prompt_text=user_prompt_text,
        )

    @staticmethod
    def _user_prompt_skill_appendix(user_prompt_text: str) -> str:
        return TaskPromptHarness.model_construct().user_prompt_skill_appendix(
            user_prompt_text
        )

    @staticmethod
    def _resolve_turn_objective(
        *,
        task: TaskEnvelope,
        user_prompt_override: str | None,
    ) -> str:
        return TaskPromptHarness.model_construct().resolve_turn_objective(
            task=task,
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
                lambda completed_task: _consume_cancelled_background_result(
                    completed_task,
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
            payload={
                "task_name": task_name,
                "error": str(exc),
                **dict(context or {}),
            },
        )
        return None


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
            payload={
                "task_name": task_name,
                "error": str(exc),
                **dict(context or {}),
            },
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


def _timeout_handoff(*, task: TaskEnvelope, timeout_seconds: float) -> TaskHandoff:
    if task.handoff is not None:
        reason = task.handoff.reason or f"timeout after {timeout_seconds:g}s"
        return task.handoff.model_copy(
            update={
                "reason": reason,
                "updated_at": datetime.now(tz=timezone.utc),
            }
        )
    return TaskHandoff(
        incomplete=(task.objective,),
        next_steps=(
            "Review the task conversation and tool history before retrying or splitting the task.",
        ),
        reason=f"timeout after {timeout_seconds:g}s",
    )
