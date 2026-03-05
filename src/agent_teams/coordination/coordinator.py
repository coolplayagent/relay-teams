from __future__ import annotations

import asyncio
import logging
from json import dumps
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.core.enums import (
    EventType,
    ExecutionMode,
    InstanceStatus,
    RunEventType,
    TaskStatus,
)
from agent_teams.core.ids import new_task_id, new_trace_id
from agent_teams.core.models import (
    EventEnvelope,
    IntentInput,
    RoleDefinition,
    RunEvent,
    TaskEnvelope,
    VerificationPlan,
)
from agent_teams.coordination.verification import verify_task
from agent_teams.coordination.task_execution_service import TaskExecutionService
from agent_teams.state.event_log import EventLog
from agent_teams.prompting.runtime_prompt_builder import RuntimePromptBuilder
from agent_teams.providers.llm import LLMProvider
from agent_teams.roles.registry import RoleRegistry
from agent_teams.logger import get_logger, log_event
from agent_teams.runtime.gate_manager import GateManager
from agent_teams.runtime.run_control_manager import RunControlManager
from agent_teams.runtime.run_event_hub import RunEventHub
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository

ROLE_COORDINATOR = "coordinator_agent"
MAX_ORCHESTRATION_CYCLES = 8
LOGGER = get_logger(__name__)


class CoordinatorGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    instance_pool: InstancePool
    task_repo: TaskRepository
    shared_store: SharedStore
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition], LLMProvider]
    task_execution_service: TaskExecutionService
    run_control_manager: RunControlManager
    gate_manager: GateManager = Field(default_factory=GateManager)
    run_event_hub: RunEventHub | None = None

    async def run(
        self,
        intent: IntentInput,
        trace_id: str | None = None,
    ) -> tuple[str, str, Literal["completed", "failed"], str]:
        trace_id = trace_id or new_trace_id().value
        session_id = intent.session_id
        if session_id is None:
            raise ValueError(
                "IntentInput.session_id is required before coordinator run"
            )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message="[coord:start] run="
            + trace_id
            + " mode="
            + intent.execution_mode.value
            + " session="
            + session_id
            + " intent="
            + intent.intent[:120],
        )

        root_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=session_id,
            parent_task_id=None,
            trace_id=trace_id,
            objective=intent.intent,
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
        _ = self.task_repo.create(root_task)
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_CREATED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=root_task.task_id,
                payload_json="{}",
            )
        )

        mode = intent.execution_mode
        if mode == ExecutionMode.MANUAL:
            result = self._initialize_manual_mode(
                trace_id=trace_id, root_task=root_task
            )
        elif mode == ExecutionMode.AI:
            coordinator_instance_id = self._ensure_coordinator_instance(
                session_id=session_id,
                trace_id=trace_id,
                root_task=root_task,
            )
            result = await self._run_ai_mode(
                trace_id=trace_id,
                root_task=root_task,
                coordinator_instance_id=coordinator_instance_id,
            )
        else:
            raise ValueError(f"Unknown execution mode: {mode}")

        verification = verify_task(self.task_repo, self.event_bus, root_task.task_id)
        status: Literal["completed", "failed"] = (
            "completed" if verification.passed else "failed"
        )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message=(
                f"[coord:finish] run={trace_id} mode={mode.value} "
                f"status={status} root_task={root_task.task_id}"
            ),
        )
        return trace_id, root_task.task_id, status, result

    def _initialize_manual_mode(self, *, trace_id: str, root_task: TaskEnvelope) -> str:
        result = (
            "Manual orchestration initialized. Use workflow APIs or tools to create a workflow and "
            'drive dispatch_tasks(action="revise"|"next").'
        )
        session_id = root_task.session_id
        self.task_repo.update_status(
            root_task.task_id, TaskStatus.COMPLETED, result=result
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_COMPLETED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=root_task.task_id,
                payload_json="{}",
            )
        )
        self._publish_run_event(
            session_id=session_id,
            run_id=trace_id,
            trace_id=trace_id,
            task_id=root_task.task_id,
            instance_id=None,
            role_id=None,
            event_type=RunEventType.AWAITING_MANUAL_ACTION,
            payload={"root_task_id": root_task.task_id},
        )
        return result

    async def _run_ai_mode(
        self,
        *,
        trace_id: str,
        root_task: TaskEnvelope,
        coordinator_instance_id: str,
    ) -> str:
        coordinator_result = await self._task_executor(
            instance_id=coordinator_instance_id,
            role_id=ROLE_COORDINATOR,
            task=root_task,
        )
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message=f"[coord:ai:first-pass-done] run={trace_id}",
        )

        cycle = 0
        while cycle < MAX_ORCHESTRATION_CYCLES:
            cycle += 1
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message=f"[coord:ai:cycle] run={trace_id} cycle={cycle}",
            )
            ran_any = await self._run_pending_delegated_tasks(
                trace_id=trace_id,
                root_task_id=root_task.task_id,
            )
            if not ran_any:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="runtime.debug",
                    message=(
                        f"[coord:ai:cycle-stop] run={trace_id} cycle={cycle} "
                        "reason=no-pending-subtasks"
                    ),
                )
                break
            coordinator_result = await self._task_executor(
                instance_id=coordinator_instance_id,
                role_id=ROLE_COORDINATOR,
                task=root_task,
            )
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message=f"[coord:ai:cycle-pass-done] run={trace_id} cycle={cycle}",
            )

        return coordinator_result

    async def _run_pending_delegated_tasks(
        self,
        *,
        trace_id: str,
        root_task_id: str,
    ) -> bool:
        records = self.task_repo.list_by_trace(trace_id)
        ran_any = False
        for record in records:
            task = record.envelope
            if task.task_id == root_task_id:
                continue
            if record.status not in (TaskStatus.ASSIGNED, TaskStatus.CREATED):
                continue
            if record.assigned_instance_id is None:
                continue
            if self.run_control_manager.is_subagent_paused(
                session_id=task.session_id,
                instance_id=record.assigned_instance_id,
            ):
                continue
            try:
                instance = self.instance_pool.get(record.assigned_instance_id)
            except KeyError:
                msg = f"Assigned instance not found: {record.assigned_instance_id}"
                self.task_repo.update_status(
                    task.task_id, TaskStatus.FAILED, error_message=msg
                )
                self.event_bus.emit(
                    EventEnvelope(
                        event_type=EventType.TASK_FAILED,
                        trace_id=task.trace_id,
                        session_id=task.session_id,
                        task_id=task.task_id,
                        instance_id=record.assigned_instance_id,
                        payload_json="{}",
                    )
                )
                continue
            try:
                _ = await self._task_executor(
                    instance_id=instance.instance_id,
                    role_id=instance.role_id,
                    task=task,
                )
            except asyncio.CancelledError:
                if self.run_control_manager.is_subagent_stop_requested(
                    run_id=trace_id,
                    instance_id=instance.instance_id,
                ):
                    continue
                raise
            ran_any = True
        return ran_any

    def _ensure_coordinator_instance(
        self,
        *,
        session_id: str,
        trace_id: str,
        root_task: TaskEnvelope,
    ) -> str:
        _ = self.role_registry.get(ROLE_COORDINATOR)
        existing_instance_id = self.agent_repo.get_coordinator_instance_id(session_id)
        known_ids = {i.instance_id for i in self.instance_pool.list_instances()}
        if existing_instance_id and existing_instance_id in known_ids:
            coordinator_instance_id = existing_instance_id
            _ = self.instance_pool.mark_idle(coordinator_instance_id)
            _ = self.agent_repo.mark_status(
                coordinator_instance_id, InstanceStatus.IDLE
            )
            self.task_repo.update_status(
                task_id=root_task.task_id,
                status=TaskStatus.ASSIGNED,
                assigned_instance_id=coordinator_instance_id,
            )
            self.event_bus.emit(
                EventEnvelope(
                    event_type=EventType.TASK_ASSIGNED,
                    trace_id=trace_id,
                    session_id=session_id,
                    task_id=root_task.task_id,
                    instance_id=coordinator_instance_id,
                    payload_json="{}",
                )
            )
            return coordinator_instance_id

        instance = self.instance_pool.create_subagent(ROLE_COORDINATOR)
        _ = self.task_repo.update_status(
            task_id=root_task.task_id,
            status=TaskStatus.ASSIGNED,
            assigned_instance_id=instance.instance_id,
        )
        self.agent_repo.upsert_instance(
            run_id=trace_id,
            trace_id=trace_id,
            session_id=session_id,
            instance_id=instance.instance_id,
            role_id=ROLE_COORDINATOR,
            status=InstanceStatus.IDLE,
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.INSTANCE_CREATED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                payload_json="{}",
            )
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_ASSIGNED,
                trace_id=trace_id,
                session_id=session_id,
                task_id=root_task.task_id,
                instance_id=instance.instance_id,
                payload_json="{}",
            )
        )
        return instance.instance_id

    def _publish_run_event(
        self,
        session_id: str,
        run_id: str,
        trace_id: str,
        task_id: str | None,
        instance_id: str | None,
        role_id: str | None,
        event_type: RunEventType,
        payload: dict[str, str],
    ) -> None:
        if self.run_event_hub is None:
            return
        self.run_event_hub.publish(
            RunEvent(
                session_id=session_id,
                run_id=run_id,
                trace_id=trace_id,
                task_id=task_id,
                instance_id=instance_id,
                role_id=role_id,
                event_type=event_type,
                payload_json=dumps(payload),
            )
        )

    async def _task_executor(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> str:
        return await self.task_execution_service.execute(
            instance_id=instance_id, role_id=role_id, task=task
        )
