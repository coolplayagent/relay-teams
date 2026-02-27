from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_teams.agents.instance_pool import InstancePool
from agent_teams.agents.subagent import SubAgentRunner
from agent_teams.core.enums import EventType, InstanceStatus, ScopeType, TaskStatus
from agent_teams.core.ids import new_task_id, new_trace_id
from agent_teams.core.models import EventEnvelope, IntentInput, RoleDefinition, ScopeRef, TaskEnvelope, VerificationPlan
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import LLMProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.events.event_bus import EventBus
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository
from agent_teams.tools.verify_task.impl import verify_task


@dataclass
class CoordinatorGraph:
    role_registry: RoleRegistry
    instance_pool: InstancePool
    task_repo: TaskRepository
    shared_store: SharedStore
    event_bus: EventBus
    agent_repo: AgentInstanceRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], LLMProvider]

    def run(self, intent: IntentInput, trace_id: str | None = None) -> tuple[str, str, str, str]:
        trace_id = trace_id or new_trace_id().value
        task = self._spec_builder(intent, trace_id)
        role_id = self._role_planner(task)
        instance_id = self._instance_creator(role_id, task, intent, trace_id)
        result = self._task_executor(instance_id, role_id, task, intent)
        verification = verify_task(self.task_repo, self.event_bus, task.task_id)
        status = 'completed' if verification.passed else 'failed'
        return trace_id, task.task_id, status, result

    def _spec_builder(self, intent: IntentInput, trace_id: str) -> TaskEnvelope:
        task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=intent.session_id,
            parent_task_id=None,
            trace_id=trace_id,
            objective=intent.intent,
            scope=('deliverable',),
            dod=('response produced',),
            verification=VerificationPlan(checklist=('non_empty_response',)),
        )
        self.task_repo.create(task)
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_CREATED,
                trace_id=trace_id,
                session_id=intent.session_id,
                task_id=task.task_id,
                payload_json='{}',
            )
        )
        return task

    def _role_planner(self, task: TaskEnvelope) -> str:
        text = task.objective.lower()
        for role in self.role_registry.list_roles():
            for capability in role.capabilities:
                if capability.lower() in text:
                    return role.role_id
        return self.role_registry.list_roles()[0].role_id

    def _instance_creator(self, role_id: str, task: TaskEnvelope, intent: IntentInput, trace_id: str) -> str:
        instance = self.instance_pool.create_subagent(role_id)
        self.task_repo.update_status(
            task_id=task.task_id,
            status=TaskStatus.ASSIGNED,
            assigned_instance_id=instance.instance_id,
        )
        self.agent_repo.upsert_instance(
            run_id=trace_id,
            trace_id=trace_id,
            session_id=intent.session_id,
            instance_id=instance.instance_id,
            role_id=role_id,
            status=InstanceStatus.IDLE,
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.INSTANCE_CREATED,
                trace_id=trace_id,
                session_id=intent.session_id,
                task_id=task.task_id,
                instance_id=instance.instance_id,
                payload_json='{}',
            )
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_ASSIGNED,
                trace_id=trace_id,
                session_id=intent.session_id,
                task_id=task.task_id,
                instance_id=instance.instance_id,
                payload_json='{}',
            )
        )
        return instance.instance_id

    def _task_executor(self, instance_id: str, role_id: str, task: TaskEnvelope, intent: IntentInput) -> str:
        self.instance_pool.mark_running(instance_id)
        self.agent_repo.mark_status(instance_id, InstanceStatus.RUNNING)
        self.task_repo.update_status(task.task_id, TaskStatus.RUNNING)
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_STARTED,
                trace_id=task.trace_id,
                session_id=intent.session_id,
                task_id=task.task_id,
                instance_id=instance_id,
                payload_json='{}',
            )
        )
        role = self.role_registry.get(role_id)
        runner = SubAgentRunner(role=role, prompt_builder=self.prompt_builder, provider=self.provider_factory(role))
        snapshot = self.shared_store.snapshot(ScopeRef(scope_type=ScopeType.SESSION, scope_id=intent.session_id))
        try:
            result = runner.run(
                task=task,
                instance_id=instance_id,
                parent_instruction=intent.parent_instruction,
                shared_state_snapshot=snapshot,
            )
            self.task_repo.update_status(task.task_id, TaskStatus.COMPLETED, result=result)
            self.instance_pool.mark_completed(instance_id)
            self.agent_repo.mark_status(instance_id, InstanceStatus.COMPLETED)
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_COMPLETED,
                    trace_id=task.trace_id,
                    session_id=intent.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
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
                    session_id=intent.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
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
                    session_id=intent.session_id,
                    task_id=task.task_id,
                    instance_id=instance_id,
                    payload_json='{}',
                )
            )
            raise
