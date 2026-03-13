# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.execution.subagent_runner import SubAgentRunner
from agent_teams.agents.enums import InstanceStatus
from agent_teams.logger import get_logger, log_event
from agent_teams.agents.execution.runtime_prompts import RuntimePromptBuilder
from agent_teams.reflection.service import ReflectionService
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.state.shared_state_repo import SharedStateRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.workspace import (
    WorkspaceManager,
    WorkspaceProfile,
    ensure_instance_workspace_profile,
)
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.events import EventEnvelope, EventType
from agent_teams.agents.tasks.models import TaskEnvelope

LOGGER = get_logger(__name__)


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
    workspace_manager: WorkspaceManager
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], object]
    injection_manager: RunInjectionManager | None = None
    run_control_manager: RunControlManager | None = None
    reflection_service: ReflectionService | None = None

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
        worker = asyncio.create_task(
            self._execute_inner(
                instance_id=instance_id,
                role_id=role_id,
                task=task,
                user_prompt_override=user_prompt_override,
            )
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
            return await worker
        finally:
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
    ) -> str:
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
        _ = self.agent_repo.mark_status(instance_id, InstanceStatus.RUNNING)
        _ = self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
        self.run_runtime_repo.ensure(
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
        self.run_runtime_repo.update(
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
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json="{}",
            )
        )

        role: RoleDefinition = self.role_registry.get(role_id)
        instance_record = self.agent_repo.get_instance(instance_id)
        workspace_profile = self._workspace_profile_for_execution(
            role_id=role_id,
            profile=role.workspace_profile,
        )
        workspace = self.workspace_manager.resolve(
            session_id=task.session_id,
            role_id=role_id,
            instance_id=instance_id,
            workspace_id=instance_record.workspace_id,
            conversation_id=instance_record.conversation_id,
            profile=workspace_profile,
        )
        role_for_run = self._role_with_memory(
            role=role,
            role_id=role_id,
            session_id=task.session_id,
            workspace_id=workspace.ref.workspace_id,
        )
        runner = SubAgentRunner(
            role=role_for_run,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role_for_run),
        )
        snapshot = workspace.memory.prompt_snapshot()
        try:
            self._ensure_committed_task_prompt(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
                instance_id=instance_id,
                task=task,
                user_prompt_override=user_prompt_override,
            )
            result = await runner.run(
                task=task,
                instance_id=instance_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
                shared_state_snapshot=snapshot,
            )
            self.task_repo.update_status(
                task.task_id, TaskStatus.COMPLETED, result=result
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.COMPLETED)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=None,
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_COMPLETED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            self._enqueue_reflection_if_needed(
                role_id=role_id,
                task=task,
                instance_id=instance_id,
                workspace_id=workspace.ref.workspace_id,
                conversation_id=workspace.ref.conversation_id,
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
            return result
        except asyncio.CancelledError:
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
                self.task_repo.update_status(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_message="Task cancelled",
                )
                self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
                self.event_bus.emit(
                    EventEnvelope(
                        event_type=EventType.TASK_FAILED,
                        trace_id=task.trace_id,
                        session_id=task.session_id,
                        task_id=task.task_id,
                        instance_id=instance_id,
                        payload_json="{}",
                    )
                )
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.STOPPED if stopped else RunRuntimeStatus.FAILED,
                phase=(
                    RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                    if paused_subagent
                    else RunRuntimePhase.TERMINAL
                    if not stopped
                    else RunRuntimePhase.IDLE
                ),
                active_instance_id=None,
                active_task_id=task.task_id if paused_subagent else None,
                active_role_id=role_id if paused_subagent else None,
                active_subagent_instance_id=(instance_id if paused_subagent else None),
                last_error="Task stopped by user" if stopped else "Task cancelled",
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
        except TimeoutError:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.TIMEOUT, error_message="Task timeout"
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.TIMEOUT)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error="Task timeout",
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_TIMEOUT,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.execution.timeout",
                message="Task execution timed out",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
            )
            raise
        except Exception as exc:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.FAILED, error_message=str(exc)
            )
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
            self.run_runtime_repo.update(
                task.trace_id,
                status=RunRuntimeStatus.FAILED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=str(exc),
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_FAILED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json="{}",
                )
            )
            log_event(
                LOGGER,
                logging.ERROR,
                event="task.execution.failed",
                message="Task execution failed",
                payload={
                    "task_id": task.task_id,
                    "instance_id": instance_id,
                    "role_id": role_id,
                },
                exc_info=exc,
            )
            raise

    def _workspace_profile_for_execution(
        self,
        *,
        role_id: str,
        profile: WorkspaceProfile,
    ) -> WorkspaceProfile:
        if self.role_registry.is_coordinator_role(role_id):
            return profile
        return ensure_instance_workspace_profile(profile)

    def _role_with_memory(
        self,
        *,
        role: RoleDefinition,
        role_id: str,
        session_id: str,
        workspace_id: str,
    ) -> RoleDefinition:
        if (
            self.role_registry.is_coordinator_role(role_id)
            or self.reflection_service is None
        ):
            return role
        memory_text = self.reflection_service.build_injected_memory(
            session_id=session_id,
            role_id=role_id,
            workspace_id=workspace_id,
        )
        if not memory_text:
            return role
        return role.model_copy(
            update={
                "system_prompt": f"{role.system_prompt}\n\n## Workspace Memory\n{memory_text}",
            }
        )

    def _enqueue_reflection_if_needed(
        self,
        *,
        role_id: str,
        task: TaskEnvelope,
        instance_id: str,
        workspace_id: str,
        conversation_id: str,
    ) -> None:
        if (
            self.role_registry.is_coordinator_role(role_id)
            or self.reflection_service is None
        ):
            return
        _ = self.reflection_service.enqueue_daily_reflection(
            session_id=task.session_id,
            run_id=task.trace_id,
            task_id=task.task_id,
            instance_id=instance_id,
            role_id=role_id,
            workspace_id=workspace_id,
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
        user_prompt_override: str | None,
    ) -> None:
        prompt = str(user_prompt_override or "").strip()
        if prompt:
            self.message_repo.append_user_prompt_if_missing(
                session_id=task.session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=role_id,
                instance_id=instance_id,
                task_id=task.task_id,
                trace_id=task.trace_id,
                content=prompt,
            )
            return

        task_history = self.message_repo.get_history_for_conversation_task(
            conversation_id,
            task.task_id,
        )
        if task_history:
            return
        self.message_repo.append_user_prompt_if_missing(
            session_id=task.session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=role_id,
            instance_id=instance_id,
            task_id=task.task_id,
            trace_id=task.trace_id,
            content=task.objective,
        )
