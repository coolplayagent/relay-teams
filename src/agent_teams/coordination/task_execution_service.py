from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_teams.agents.instance_pool import InstancePool
from agent_teams.agents.subagent import SubAgentRunner
from agent_teams.core.enums import EventType, InstanceStatus, ScopeType, TaskStatus
from agent_teams.core.models import EventEnvelope, RoleDefinition, ScopeRef, TaskEnvelope
from agent_teams.events.event_bus import EventBus
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import LLMProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.runtime.console import log_debug
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository

ROLE_COORDINATOR = 'coordinator_agent'


@dataclass
class TaskExecutionService:
    role_registry: RoleRegistry
    instance_pool: InstancePool
    task_repo: TaskRepository
    shared_store: SharedStore
    event_bus: EventBus
    agent_repo: AgentInstanceRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], LLMProvider]

    def execute(self, *, instance_id: str, role_id: str, task: TaskEnvelope) -> str:
        log_debug(
            f'[subagent:start] run={task.trace_id} task={task.task_id} '
            f'instance={instance_id} role={role_id}'
        )
        self.instance_pool.mark_running(instance_id)
        self.agent_repo.mark_status(instance_id, InstanceStatus.RUNNING)
        self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=task.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json='{}',
            )
        )

        role = self.role_registry.get(role_id)
        runner = SubAgentRunner(role=role, prompt_builder=self.prompt_builder, provider=self.provider_factory(role))
        snapshot = (
            ()
            if role_id == ROLE_COORDINATOR
            else self.shared_store.snapshot(ScopeRef(scope_type=ScopeType.SESSION, scope_id=task.session_id))
        )
        try:
            result = runner.run(
                task=task,
                instance_id=instance_id,
                parent_instruction=task.parent_instruction,
                shared_state_snapshot=snapshot,
            )
            self.task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result=result)
            self.instance_pool.mark_completed(instance_id)
            self.agent_repo.mark_status(instance_id, InstanceStatus.COMPLETED)
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_COMPLETED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
            )
            log_debug(
                f'[subagent:done] run={task.trace_id} task={task.task_id} '
                f'instance={instance_id} role={role_id}'
            )
            return result
        except TimeoutError:
            self.task_repo.update_status(task.task_id, TaskStatus.TIMEOUT, error_message='Task timeout')
            self.instance_pool.mark_timeout(instance_id)
            self.agent_repo.mark_status(instance_id, InstanceStatus.TIMEOUT)
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_TIMEOUT,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
            )
            log_debug(
                f'[subagent:timeout] run={task.trace_id} task={task.task_id} '
                f'instance={instance_id} role={role_id}'
            )
            raise
        except Exception as exc:
            self.task_repo.update_status(task.task_id, TaskStatus.FAILED, error_message=str(exc))
            self.instance_pool.mark_failed(instance_id)
            self.agent_repo.mark_status(instance_id, InstanceStatus.FAILED)
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_FAILED,
                    trace_id=task.trace_id,
                    session_id=task.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
            )
            log_debug(
                f'[subagent:error] run={task.trace_id} task={task.task_id} '
                f'instance={instance_id} role={role_id} err={exc}'
            )
            raise
