from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.models import create_subagent_instance
from agent_teams.agents.orchestration.coordinator import CoordinatorGraph
from agent_teams.agents.execution.runtime_prompts import RuntimePromptBuilder
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.sessions.runs.control import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.models import IntentInput
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.models import (
    TaskEnvelope,
    VerificationPlan,
    VerificationResult,
)


class _RecordingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[str] = []

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> str:
        _ = role_id
        self.calls.append(task.task_id)
        result = f"{task.task_id} done"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return result


def _build_coordinator(
    tmp_path: Path,
    *,
    coordinator_role_id: str = "coordinator_agent",
) -> tuple[
    CoordinatorGraph,
    TaskRepository,
    AgentInstanceRepository,
    RunRuntimeRepository,
    _RecordingTaskExecutionService,
]:
    db_path = tmp_path / "coordinator_resume_recovery.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id=coordinator_role_id,
            name="Coordinator Agent",
            version="1",
            tools=(
                "list_available_roles",
                "create_tasks",
                "update_task",
                "list_run_tasks",
                "dispatch_task",
            ),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="time",
            name="time",
            version="1",
            system_prompt="Tell the current time.",
        )
    )
    task_execution_service = _RecordingTaskExecutionService(task_repo)
    run_control_manager = RunControlManager()
    run_control_manager.bind_runtime(
        run_event_hub=RunEventHub(),
        injection_manager=RunInjectionManager(),
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=message_repo,
        event_bus=event_log,
        run_runtime_repo=run_runtime_repo,
    )
    coordinator = CoordinatorGraph.model_construct(
        role_registry=role_registry,
        task_repo=task_repo,
        shared_store=SharedStateRepository(db_path),
        event_bus=event_log,
        agent_repo=agent_repo,
        prompt_builder=RuntimePromptBuilder(),
        provider_factory=lambda _: None,
        task_execution_service=task_execution_service,
        run_runtime_repo=run_runtime_repo,
        run_control_manager=run_control_manager,
    )
    return (
        coordinator,
        task_repo,
        agent_repo,
        run_runtime_repo,
        task_execution_service,
    )


def test_terminal_status_from_verification_marks_root_task_failed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coordinator_terminal_status.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    task_repo.update_status(
        root_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id="inst-1",
    )
    coordinator = CoordinatorGraph.model_construct(
        task_repo=task_repo,
        event_bus=event_log,
    )

    status = coordinator._terminal_status_from_verification(
        trace_id="run-1",
        root_task=root_task,
        verification=VerificationResult(
            task_id=root_task.task_id,
            passed=False,
            details=("Task not completed yet",),
        ),
        output="",
    )

    assert status == "failed"
    record = task_repo.get(root_task.task_id)
    assert record.status == TaskStatus.FAILED
    assert record.assigned_instance_id == "inst-1"
    assert record.error_message == "Task not completed yet"

    events = event_log.list_by_session("session-1")
    assert events[-1]["event_type"] == "task_failed"
    payload = json.loads(str(events[-1]["payload_json"]))
    assert payload["reason"] == "verification_failed"
    assert payload["details"] == ["Task not completed yet"]


@pytest.mark.asyncio
async def test_resume_reactivates_stopped_delegated_task_before_verification(
    tmp_path: Path,
) -> None:
    (
        coordinator,
        task_repo,
        agent_repo,
        run_runtime_repo,
        task_execution_service,
    ) = _build_coordinator(tmp_path)
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    child_task = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(child_task)

    coordinator_instance = create_subagent_instance("coordinator_agent")
    child_instance = create_subagent_instance("time")

    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=coordinator_instance.instance_id,
        role_id="coordinator_agent",
        workspace_id=coordinator_instance.workspace_id,
        conversation_id=coordinator_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=child_instance.instance_id,
        role_id="time",
        workspace_id=child_instance.workspace_id,
        conversation_id=child_instance.conversation_id,
        status=InstanceStatus.STOPPED,
    )
    task_repo.update_status(
        root_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=coordinator_instance.instance_id,
    )
    task_repo.update_status(
        child_task.task_id,
        TaskStatus.STOPPED,
        assigned_instance_id=child_instance.instance_id,
        error_message="Task stopped by user",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.STOPPED,
            phase=RunRuntimePhase.IDLE,
        )
    )

    trace_id, root_task_id, status, result = await coordinator.resume(trace_id="run-1")

    assert (trace_id, root_task_id, status, result) == (
        "run-1",
        root_task.task_id,
        "completed",
        "task-root-1 done",
    )
    assert task_execution_service.calls == [child_task.task_id, root_task.task_id]
    assert task_repo.get(child_task.task_id).status == TaskStatus.COMPLETED
    assert task_repo.get(root_task.task_id).status == TaskStatus.COMPLETED
    assert (
        agent_repo.get_instance(child_instance.instance_id).status
        == InstanceStatus.IDLE
    )


def test_prepare_recovery_preserves_paused_subagent_followup_state(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    child_task = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(child_task)

    coordinator_instance = create_subagent_instance("coordinator_agent")
    child_instance = create_subagent_instance("time")

    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=coordinator_instance.instance_id,
        role_id="coordinator_agent",
        workspace_id=coordinator_instance.workspace_id,
        conversation_id=coordinator_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=child_instance.instance_id,
        role_id="time",
        workspace_id=child_instance.workspace_id,
        conversation_id=child_instance.conversation_id,
        status=InstanceStatus.STOPPED,
    )
    task_repo.update_status(
        root_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=coordinator_instance.instance_id,
    )
    task_repo.update_status(
        child_task.task_id,
        TaskStatus.STOPPED,
        assigned_instance_id=child_instance.instance_id,
        error_message="Task stopped by user",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.STOPPED,
            phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
            active_task_id=child_task.task_id,
            active_role_id="time",
            active_subagent_instance_id=child_instance.instance_id,
            last_error="Subagent stopped by user",
        )
    )

    coordinator._prepare_recovery(
        trace_id="run-1",
        coordinator_instance_id=coordinator_instance.instance_id,
    )

    child_record = task_repo.get(child_task.task_id)
    assert child_record.status == TaskStatus.STOPPED
    assert child_record.error_message == "Task stopped by user"
    assert (
        agent_repo.get_instance(child_instance.instance_id).status
        == InstanceStatus.STOPPED
    )
    assert (
        coordinator._has_resumable_delegated_work(
            trace_id="run-1",
            root_task_id=root_task.task_id,
        )
        is False
    )


@pytest.mark.asyncio
async def test_run_resolves_dynamic_coordinator_role_id(tmp_path: Path) -> None:
    coordinator, task_repo, agent_repo, _, _ = _build_coordinator(
        tmp_path,
        coordinator_role_id="Coordinator",
    )

    trace_id, root_task_id, status, result = await coordinator.run(
        IntentInput(session_id="session-1", intent="hello"),
        trace_id="run-dynamic",
    )

    root_task = task_repo.get(root_task_id)
    coordinator_instance = agent_repo.get_session_role_instance(
        "session-1", "Coordinator"
    )

    assert trace_id == "run-dynamic"
    assert status == "completed"
    assert result == f"{root_task_id} done"
    assert root_task.envelope.role_id == "Coordinator"
    assert coordinator_instance is not None
    assert coordinator_instance.role_id == "Coordinator"
