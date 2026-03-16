# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.execution.subagent_runner import SubAgentRunner
from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.logger import get_logger, log_event
from agent_teams.agents.execution.system_prompts import RuntimePromptBuilder
from agent_teams.persistence.scope_models import ScopeRef, ScopeType
from agent_teams.roles.memory_injection import build_role_with_memory
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_models import RunThinkingConfig
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.workspace import WorkspaceManager
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
    role_memory_service: RoleMemoryService | None = None
    run_intent_repo: RunIntentRepository | None = None

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
        runner = SubAgentRunner(
            role=role_for_run,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role_for_run),
        )
        snapshot = self._shared_state_snapshot(
            session_id=task.session_id,
            role_id=role_id,
            conversation_id=workspace.ref.conversation_id,
        )
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
                working_directory=workspace.resolve_workdir(),
                conversation_id=workspace.ref.conversation_id,
                shared_state_snapshot=snapshot,
                thinking=self._thinking_for_run(task.trace_id),
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
            self._record_memory_if_needed(
                role_id=role_id,
                workspace_id=workspace.ref.workspace_id,
                task=task,
                conversation_id=workspace.ref.conversation_id,
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

    def _thinking_for_run(self, run_id: str) -> RunThinkingConfig:
        if self.run_intent_repo is None:
            return RunThinkingConfig()
        try:
            return self.run_intent_repo.get(run_id).thinking
        except KeyError:
            return RunThinkingConfig()

    def _role_with_memory(
        self,
        *,
        role: RoleDefinition,
        role_id: str,
        workspace_id: str,
    ) -> RoleDefinition:
        return build_role_with_memory(
            role_registry=self.role_registry,
            role_memory_service=self.role_memory_service,
            role=role,
            role_id=role_id,
            workspace_id=workspace_id,
        )

    def _record_memory_if_needed(
        self,
        *,
        role_id: str,
        workspace_id: str,
        task: TaskEnvelope,
        conversation_id: str,
        result: str,
    ) -> None:
        del role_id, workspace_id, task, conversation_id, result
        return

    def _shared_state_snapshot(
        self,
        *,
        session_id: str,
        role_id: str,
        conversation_id: str,
    ) -> tuple[tuple[str, str], ...]:
        scopes = (
            ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id),
            ScopeRef(scope_type=ScopeType.ROLE, scope_id=f"{session_id}:{role_id}"),
            ScopeRef(scope_type=ScopeType.CONVERSATION, scope_id=conversation_id),
        )
        return self.shared_store.snapshot_many(scopes)

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
