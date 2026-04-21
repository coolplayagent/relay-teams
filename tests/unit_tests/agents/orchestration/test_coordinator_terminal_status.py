from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.media import content_parts_from_text
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionResult
from relay_teams.agents.execution.system_prompts import RuntimePromptBuilder
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_models import IntentInput
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    VerificationPlan,
    VerificationResult,
)
from relay_teams.workspace import (
    build_conversation_id,
    build_instance_conversation_id,
)
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookService


class _RecordingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[str] = []

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> TaskExecutionResult:
        _ = role_id
        self.calls.append(task.task_id)
        result = f"{task.task_id} done"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return TaskExecutionResult(output=result)


class _CapturingHookService(HookService):
    def __init__(self, decision: HookDecisionType = HookDecisionType.ALLOW) -> None:
        self.events: list[object] = []
        self._decision = decision

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = run_event_hub
        self.events.append(event_input)
        return HookDecisionBundle(
            decision=self._decision,
            reason=(
                "Verification completion denied"
                if self._decision == HookDecisionType.DENY
                else ""
            ),
        )


def _build_coordinator(
    tmp_path: Path,
    *,
    coordinator_role_id: str = "Coordinator",
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
    session_repo = SessionRepository(db_path)
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id=coordinator_role_id,
            name="Coordinator Agent",
            description="Coordinates delegated work.",
            version="1",
            tools=(
                "create_tasks",
                "update_task",
                "dispatch_task",
            ),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="time",
            name="time",
            description="Reports the current time.",
            version="1",
            system_prompt="Tell the current time.",
        )
    )
    task_execution_service = _RecordingTaskExecutionService(task_repo)
    _ = session_repo.create(session_id="session-1", workspace_id="default")
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
        prompt_builder=RuntimePromptBuilder(
            role_registry=role_registry,
            mcp_registry=McpRegistry(),
        ),
        provider_factory=lambda _, __=None: None,
        task_execution_service=task_execution_service,
        run_runtime_repo=run_runtime_repo,
        run_control_manager=run_control_manager,
        session_repo=session_repo,
    )
    return (
        coordinator,
        task_repo,
        agent_repo,
        run_runtime_repo,
        task_execution_service,
    )


@pytest.mark.asyncio
async def test_terminal_status_from_verification_completes_with_assistant_error(
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
    hook_service = _CapturingHookService()
    coordinator = CoordinatorGraph.model_construct(
        task_repo=task_repo,
        event_bus=event_log,
        hook_service=hook_service,
        run_event_hub=RunEventHub(),
    )

    result = await coordinator._terminal_status_from_verification(
        trace_id="run-1",
        root_task=root_task,
        verification=VerificationResult(
            task_id=root_task.task_id,
            passed=False,
            details=("Task not completed yet",),
        ),
        output="",
        root_instance_id=None,
        root_role_id="Coordinator",
    )

    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "verification_failed"
    assert result.error_message == "Task not completed yet"
    record = task_repo.get(root_task.task_id)
    assert record.status == TaskStatus.COMPLETED
    assert record.assigned_instance_id == "inst-1"
    assert record.error_message == "Task not completed yet"
    assert "Task not completed yet" in (record.result or "")
    assert len(hook_service.events) == 1

    events = event_log.list_by_session("session-1")
    assert events == ()


@pytest.mark.asyncio
async def test_terminal_status_from_verification_respects_task_completed_deny(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coordinator_terminal_status_deny.db"
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
        hook_service=_CapturingHookService(HookDecisionType.DENY),
        run_event_hub=RunEventHub(),
    )

    result = await coordinator._terminal_status_from_verification(
        trace_id="run-1",
        root_task=root_task,
        verification=VerificationResult(
            task_id=root_task.task_id,
            passed=False,
            details=("Task not completed yet",),
        ),
        output="",
        root_instance_id=None,
        root_role_id="Coordinator",
    )

    record = task_repo.get(root_task.task_id)
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "task_completion_denied"
    assert result.error_message == "Verification completion denied"
    assert record.status == TaskStatus.FAILED
    assert record.error_message == "Verification completion denied"


@pytest.mark.asyncio
async def test_initialize_manual_mode_denied_completion_marks_task_failed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "coordinator_manual_mode_deny.db"
    task_repo = TaskRepository(db_path)
    event_log = EventLog(db_path)
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator = CoordinatorGraph.model_construct(
        task_repo=task_repo,
        event_bus=event_log,
        hook_service=_CapturingHookService(HookDecisionType.DENY),
        run_event_hub=RunEventHub(),
    )

    result = await coordinator._initialize_manual_mode(
        trace_id="run-1",
        root_task=root_task,
    )

    record = task_repo.get(root_task.task_id)
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "task_completion_denied"
    assert result.error_message == "Verification completion denied"
    assert record.status == TaskStatus.FAILED
    assert record.error_message == "Verification completion denied"


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

    coordinator_instance = create_subagent_instance(
        "Coordinator",
        workspace_id="workspace-1",
        conversation_id="conversation-coordinator",
    )
    child_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time",
    )

    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=coordinator_instance.instance_id,
        role_id="Coordinator",
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

    result = await coordinator.resume(trace_id="run-1")

    assert result.trace_id == "run-1"
    assert result.root_task_id == root_task.task_id
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert result.output == "task-root-1 done"
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

    coordinator_instance = create_subagent_instance(
        "Coordinator",
        workspace_id="workspace-1",
        conversation_id="conversation-coordinator",
    )
    child_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time",
    )

    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=coordinator_instance.instance_id,
        role_id="Coordinator",
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

    result = await coordinator.run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("hello"),
        ),
        trace_id="run-dynamic",
    )

    root_task = task_repo.get(result.root_task_id)
    coordinator_instance = agent_repo.get_session_role_instance(
        "session-1", "Coordinator"
    )

    assert result.trace_id == "run-dynamic"
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert result.output == f"{result.root_task_id} done"
    assert root_task.envelope.role_id == "Coordinator"
    assert coordinator_instance is not None
    assert coordinator_instance.role_id == "Coordinator"


@pytest.mark.asyncio
async def test_run_with_fresh_root_instance_skips_stale_session_role_instance(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path,
        coordinator_role_id="Coordinator",
    )
    stale_instance = create_subagent_instance(
        "Coordinator",
        workspace_id="default",
        conversation_id=build_conversation_id("session-1", "Coordinator"),
    )
    agent_repo.upsert_instance(
        run_id="run-stale",
        trace_id="run-stale",
        session_id="session-1",
        instance_id=stale_instance.instance_id,
        role_id="Coordinator",
        workspace_id=stale_instance.workspace_id,
        conversation_id=stale_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )

    result = await coordinator.run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("hello"),
            reuse_root_instance=False,
        ),
        trace_id="run-fresh",
    )

    root_task = task_repo.get(result.root_task_id)
    assigned_instance_id = root_task.assigned_instance_id
    assert result.trace_id == "run-fresh"
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert assigned_instance_id is not None
    assert assigned_instance_id != stale_instance.instance_id
    runtime_record = agent_repo.get_instance(assigned_instance_id)
    assert runtime_record.conversation_id != stale_instance.conversation_id
    assert runtime_record.conversation_id == build_instance_conversation_id(
        "session-1",
        "Coordinator",
        assigned_instance_id,
    )
