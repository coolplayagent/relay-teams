# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
from json import dumps
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.agents.instances.models import create_subagent_instance
from agent_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from agent_teams.agents.orchestration.verification import verify_task
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.system_prompts import RuntimePromptBuilder
from agent_teams.providers.provider_contracts import LLMProvider
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.logger import get_logger, log_event
from agent_teams.agents.orchestration.human_gate import GateManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.enums import ExecutionMode, RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.ids import new_trace_id
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
)
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.workspace import build_conversation_id
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.events import EventEnvelope, EventType
from agent_teams.agents.tasks.ids import new_task_id
from agent_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    VerificationPlan,
    VerificationResult,
)
from agent_teams.sessions.session_models import SessionMode

MAX_ORCHESTRATION_CYCLES = 8
LOGGER = get_logger(__name__)


class CoordinatorGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    role_registry: RoleRegistry
    task_repo: TaskRepository
    shared_store: SharedStateRepository
    event_bus: EventLog
    agent_repo: AgentInstanceRepository
    prompt_builder: RuntimePromptBuilder
    provider_factory: Callable[[RoleDefinition, str | None], LLMProvider]
    task_execution_service: TaskExecutionService
    run_runtime_repo: RunRuntimeRepository
    run_control_manager: RunControlManager
    session_repo: SessionRepository | None = None
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
            logging.INFO,
            event="coord.run.started",
            message="Coordinator run started",
            payload={
                "execution_mode": intent.execution_mode.value,
                "session_mode": intent.session_mode.value,
                "session_id": session_id,
                "intent_preview": intent.intent[:120],
            },
        )
        root_role_id = self._root_role_id(intent)

        root_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=session_id,
            parent_task_id=None,
            trace_id=trace_id,
            role_id=root_role_id,
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
            root_instance_id = self._ensure_root_instance(
                session_id=session_id,
                trace_id=trace_id,
                root_task=root_task,
            )
            if intent.session_mode == SessionMode.NORMAL:
                result = await self._task_executor(
                    instance_id=root_instance_id,
                    role_id=root_role_id,
                    task=root_task,
                )
            else:
                result = await self._run_ai_mode(
                    trace_id=trace_id,
                    root_task=root_task,
                    coordinator_instance_id=root_instance_id,
                )
        else:
            raise ValueError(f"Unknown execution mode: {mode}")

        verification = verify_task(self.task_repo, self.event_bus, root_task.task_id)
        status = self._terminal_status_from_verification(
            trace_id=trace_id,
            root_task=root_task,
            verification=verification,
            output=result,
        )
        log_event(
            LOGGER,
            logging.INFO if status == "completed" else logging.WARNING,
            event="coord.run.completed"
            if status == "completed"
            else "coord.run.failed",
            message="Coordinator run finished",
            payload={
                "execution_mode": mode.value,
                "status": status,
                "root_task_id": root_task.task_id,
            },
        )
        return trace_id, root_task.task_id, status, result

    async def resume(
        self,
        *,
        trace_id: str,
    ) -> tuple[str, str, Literal["completed", "failed"], str]:
        root_task_record = self._get_root_task_by_trace(trace_id)
        root_task = root_task_record.envelope
        root_instance_id = self._ensure_root_instance(
            session_id=root_task.session_id,
            trace_id=trace_id,
            root_task=root_task,
        )
        self._prepare_recovery(
            trace_id=trace_id,
            coordinator_instance_id=root_instance_id,
        )
        root_role_id = _require_task_role_id(root_task)
        if not self.role_registry.is_coordinator_role(root_role_id):
            result = await self._task_executor(
                instance_id=root_instance_id,
                role_id=root_role_id,
                task=root_task,
            )
            verification = verify_task(
                self.task_repo, self.event_bus, root_task.task_id
            )
            status = self._terminal_status_from_verification(
                trace_id=trace_id,
                root_task=root_task,
                verification=verification,
                output=result,
            )
            return trace_id, root_task.task_id, status, result
        runtime = self.run_runtime_repo.get(trace_id)
        coordinator_first = not self._has_resumable_delegated_work(
            trace_id=trace_id,
            root_task_id=root_task.task_id,
        )
        if runtime is not None and runtime.phase in {
            RunRuntimePhase.SUBAGENT_RUNNING,
            RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        }:
            coordinator_first = False
        result = await self._run_ai_mode(
            trace_id=trace_id,
            root_task=root_task,
            coordinator_instance_id=root_instance_id,
            coordinator_first=coordinator_first,
            initial_result=root_task_record.result or "",
        )
        verification = verify_task(self.task_repo, self.event_bus, root_task.task_id)
        status = self._terminal_status_from_verification(
            trace_id=trace_id,
            root_task=root_task,
            verification=verification,
            output=result,
        )
        return trace_id, root_task.task_id, status, result

    def _initialize_manual_mode(self, *, trace_id: str, root_task: TaskEnvelope) -> str:
        result = (
            "Manual orchestration initialized. Use task APIs or task tools to create, update, "
            "list, and dispatch delegated tasks."
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
        coordinator_first: bool = True,
        initial_result: str = "",
    ) -> str:
        coordinator_result = initial_result
        coordinator_role_id = _require_task_role_id(root_task)
        if coordinator_first:
            coordinator_result = await self._task_executor(
                instance_id=coordinator_instance_id,
                role_id=coordinator_role_id,
                task=root_task,
            )
            log_event(
                LOGGER,
                logging.DEBUG,
                event="coord.cycle.first_pass.completed",
                message="Coordinator first pass completed",
            )

        cycle = 0
        while cycle < MAX_ORCHESTRATION_CYCLES:
            cycle += 1
            log_event(
                LOGGER,
                logging.DEBUG,
                event="coord.cycle.started",
                message="Coordinator cycle started",
                payload={"cycle": cycle},
            )
            ran_any = await self._run_pending_delegated_tasks(
                trace_id=trace_id,
                root_task_id=root_task.task_id,
            )
            if not ran_any:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="coord.cycle.stopped",
                    message="Coordinator cycle stopped",
                    payload={
                        "cycle": cycle,
                        "reason": "no_pending_subtasks",
                    },
                )
                break
            coordinator_result = await self._task_executor(
                instance_id=coordinator_instance_id,
                role_id=coordinator_role_id,
                task=root_task,
            )
            log_event(
                LOGGER,
                logging.DEBUG,
                event="coord.cycle.completed",
                message="Coordinator cycle completed",
                payload={"cycle": cycle},
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
                instance = self.agent_repo.get_instance(record.assigned_instance_id)
            except KeyError:
                msg = f"Assigned instance not found: {record.assigned_instance_id}"
                self.task_repo.update_status(
                    task.task_id, TaskStatus.FAILED, error_message=msg
                )
                log_event(
                    LOGGER,
                    logging.ERROR,
                    event="coord.task.failed",
                    message="Assigned instance missing for delegated task",
                    payload={
                        "task_id": task.task_id,
                        "assigned_instance_id": record.assigned_instance_id,
                    },
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

    def _get_root_task_by_trace(self, trace_id: str) -> TaskRecord:
        for record in self.task_repo.list_by_trace(trace_id):
            if record.envelope.parent_task_id is None:
                return record
        raise KeyError(f"No root task found for run_id={trace_id}")

    def _prepare_recovery(self, *, trace_id: str, coordinator_instance_id: str) -> None:
        runtime = self.run_runtime_repo.get(trace_id)
        records = self.task_repo.list_by_trace(trace_id)
        incomplete_task_ids = {
            record.envelope.task_id
            for record in records
            if record.status
            not in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT}
        }
        for record in records:
            if record.status == TaskStatus.RUNNING or (
                record.status == TaskStatus.STOPPED
                and not self._is_paused_subagent_task(
                    runtime=runtime,
                    task_id=record.envelope.task_id,
                    assigned_instance_id=record.assigned_instance_id,
                )
            ):
                next_status = (
                    TaskStatus.ASSIGNED
                    if record.assigned_instance_id
                    else TaskStatus.CREATED
                )
                self.task_repo.update_status(
                    record.envelope.task_id,
                    next_status,
                    assigned_instance_id=record.assigned_instance_id,
                )

        for instance in self.agent_repo.list_by_run(trace_id):
            should_reset = (
                instance.instance_id == coordinator_instance_id
                or instance.status == InstanceStatus.RUNNING
                or any(
                    record.assigned_instance_id == instance.instance_id
                    and record.envelope.task_id in incomplete_task_ids
                    for record in records
                )
            )
            if (
                runtime is not None
                and runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
                and runtime.active_subagent_instance_id == instance.instance_id
            ):
                should_reset = False
            if not should_reset:
                continue
            self.agent_repo.mark_status(instance.instance_id, InstanceStatus.IDLE)

    def _has_resumable_delegated_work(
        self, *, trace_id: str, root_task_id: str
    ) -> bool:
        runtime = self.run_runtime_repo.get(trace_id)
        for record in self.task_repo.list_by_trace(trace_id):
            task = record.envelope
            if task.task_id == root_task_id:
                continue
            if record.status not in {
                TaskStatus.CREATED,
                TaskStatus.ASSIGNED,
                TaskStatus.RUNNING,
                TaskStatus.STOPPED,
            }:
                continue
            if record.assigned_instance_id is None:
                continue
            if self._is_paused_subagent_task(
                runtime=runtime,
                task_id=task.task_id,
                assigned_instance_id=record.assigned_instance_id,
            ):
                continue
            if self.run_control_manager.is_subagent_paused(
                session_id=task.session_id,
                instance_id=record.assigned_instance_id,
            ):
                continue
            return True
        return False

    def _is_paused_subagent_task(
        self,
        *,
        runtime: RunRuntimeRecord | None,
        task_id: str,
        assigned_instance_id: str | None,
    ) -> bool:
        if (
            runtime is None
            or runtime.phase != RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
        ):
            return False
        if runtime.active_task_id and runtime.active_task_id == task_id:
            return True
        if (
            assigned_instance_id is not None
            and runtime.active_subagent_instance_id == assigned_instance_id
        ):
            return True
        return False

    def _ensure_root_instance(
        self,
        *,
        session_id: str,
        trace_id: str,
        root_task: TaskEnvelope,
    ) -> str:
        root_role_id = _require_task_role_id(root_task)
        _ = self.role_registry.get(root_role_id)
        existing = self.agent_repo.get_session_role_instance(session_id, root_role_id)
        if existing is not None:
            coordinator_instance_id = existing.instance_id
            _ = self.agent_repo.mark_status(
                coordinator_instance_id, InstanceStatus.IDLE
            )
            self.agent_repo.upsert_instance(
                run_id=trace_id,
                trace_id=trace_id,
                session_id=session_id,
                instance_id=coordinator_instance_id,
                role_id=root_role_id,
                workspace_id=existing.workspace_id,
                conversation_id=existing.conversation_id,
                status=InstanceStatus.IDLE,
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

        session = self.session_repo.get(session_id) if self.session_repo else None
        if session is None:
            raise RuntimeError(
                "CoordinatorGraph requires session_repo to resolve workspace"
            )
        workspace_id = session.workspace_id
        conversation_id = build_conversation_id(session_id, root_role_id)
        instance = create_subagent_instance(
            root_role_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
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
            role_id=root_role_id,
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
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

    def _root_role_id(self, intent: IntentInput) -> str:
        if intent.target_role_id:
            return intent.target_role_id
        topology = intent.topology
        if topology is None:
            return self.role_registry.get_coordinator_role_id()
        if topology.session_mode == SessionMode.NORMAL:
            return topology.normal_root_role_id
        return topology.coordinator_role_id

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

    def _terminal_status_from_verification(
        self,
        *,
        trace_id: str,
        root_task: TaskEnvelope,
        verification: VerificationResult,
        output: str,
    ) -> Literal["completed", "failed"]:
        passed = bool(getattr(verification, "passed", False))
        if passed:
            return "completed"

        details = tuple(
            str(item) for item in getattr(verification, "details", ()) if str(item)
        )
        failure_message = (
            "; ".join(details)
            if details
            else (output.strip() if output.strip() else "Verification failed")
        )
        current = self.task_repo.get(root_task.task_id)
        self.task_repo.update_status(
            root_task.task_id,
            TaskStatus.FAILED,
            assigned_instance_id=current.assigned_instance_id,
            result=current.result or output or None,
            error_message=failure_message,
        )
        self.event_bus.emit(
            EventEnvelope(
                event_type=EventType.TASK_FAILED,
                trace_id=trace_id,
                session_id=root_task.session_id,
                task_id=root_task.task_id,
                instance_id=current.assigned_instance_id,
                payload_json=dumps(
                    {
                        "reason": "verification_failed",
                        "details": list(details),
                    }
                ),
            )
        )
        return "failed"

    async def _task_executor(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> str:
        return await self.task_execution_service.execute(
            instance_id=instance_id, role_id=role_id, task=task
        )


def _require_task_role_id(task: TaskEnvelope) -> str:
    role_id = task.role_id
    if role_id is None:
        raise ValueError(f"Task {task.task_id} is not bound to a role")
    return role_id
