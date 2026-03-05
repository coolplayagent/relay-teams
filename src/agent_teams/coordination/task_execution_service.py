from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.agents.core.subagent import SubAgentRunner
from agent_teams.core.enums import EventType, InstanceStatus, ScopeType, TaskStatus
from agent_teams.core.models import (
    EventEnvelope,
    RoleDefinition,
    ScopeRef,
    TaskEnvelope,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.injection_manager import RunInjectionManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository

ROLE_COORDINATOR = "coordinator_agent"
LOGGER = get_logger(__name__)


class TaskExecutionService(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    instance_pool: InstancePool
    task_repo: TaskRepository
    shared_store: SharedStore
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    message_repo: MessageRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], object]
    injection_manager: RunInjectionManager | None = None
    run_control_manager: RunControlManager | None = None

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> str:
        worker = asyncio.create_task(
            self._execute_inner(instance_id=instance_id, role_id=role_id, task=task)
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
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> str:
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message="[subagent:start] run="
            + task.trace_id
            + " task="
            + task.task_id
            + " instance="
            + instance_id
            + " role="
            + role_id,
        )
        _ = self.instance_pool.mark_running(instance_id)
        _ = self.agent_repo.mark_status(instance_id, InstanceStatus.RUNNING)
        _ = self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
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
        runner = SubAgentRunner(
            role=role,
            prompt_builder=self.prompt_builder,
            provider=self.provider_factory(role),
        )
        snapshot = (
            ()
            if role_id == ROLE_COORDINATOR
            else self.shared_store.snapshot(
                ScopeRef(scope_type=ScopeType.SESSION, scope_id=task.session_id)
            )
        )
        try:
            result = await runner.run(
                task=task,
                instance_id=instance_id,
                shared_state_snapshot=snapshot,
            )
            self.task_repo.update_status(
                task.task_id, TaskStatus.COMPLETED, result=result
            )
            _ = self.instance_pool.mark_completed(instance_id)
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.COMPLETED)
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
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message="[subagent:done] run="
                + task.trace_id
                + " task="
                + task.task_id
                + " instance="
                + instance_id
                + " role="
                + role_id,
            )
            return result
        except asyncio.CancelledError:
            if self.run_control_manager is not None:
                stopped = self.run_control_manager.handle_instance_cancelled(
                    task=task,
                    instance_id=instance_id,
                )
            else:
                stopped = False
                self.task_repo.update_status(
                    task.task_id,
                    TaskStatus.FAILED,
                    error_message="Task cancelled",
                )
                _ = self.instance_pool.mark_failed(instance_id)
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
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message="[subagent:"
                + ("stopped" if stopped else "cancelled")
                + "] run="
                + task.trace_id
                + " task="
                + task.task_id
                + " instance="
                + instance_id
                + " role="
                + role_id,
            )
            raise
        except TimeoutError:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.TIMEOUT, error_message="Task timeout"
            )
            _ = self.instance_pool.mark_timeout(instance_id)
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.TIMEOUT)
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
                logging.DEBUG,
                event="runtime.debug",
                message="[subagent:timeout] run="
                + task.trace_id
                + " task="
                + task.task_id
                + " instance="
                + instance_id
                + " role="
                + role_id,
            )
            raise
        except Exception as exc:
            _ = self.task_repo.update_status(
                task.task_id, TaskStatus.FAILED, error_message=str(exc)
            )
            _ = self.instance_pool.mark_failed(instance_id)
            _ = self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
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
                logging.DEBUG,
                event="runtime.debug",
                message=(
                    f"[subagent:error] run={task.trace_id} task={task.task_id} "
                    f"instance={instance_id} role={role_id} err={exc}"
                ),
            )
            raise
