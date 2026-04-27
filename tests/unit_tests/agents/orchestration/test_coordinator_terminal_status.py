from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from relay_teams.media import content_parts_from_text
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import create_subagent_instance
from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.agents.execution.system_prompts import RuntimePromptBuilder
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
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
    WorkspaceManager,
    build_conversation_id,
    build_instance_conversation_id,
)
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookService,
    TaskCreatedInput,
)


class _RecordingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.runtime_role_resolver: RuntimeRoleResolver | None = None
        self.workspace_manager: WorkspaceManager | None = None
        self.run_intent_repo: RunIntentRepository | None = None
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


class _FailingRunIntentRepository(RunIntentRepository):
    async def get_async(
        self, run_id: str, *, fallback_session_id: str | None = None
    ) -> IntentInput:
        _ = (run_id, fallback_session_id)
        raise RuntimeError("invalid persisted intent")


class _SlowRecordingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[str] = []
        self.spans: dict[str, tuple[float, float]] = {}
        self.active_count = 0
        self.max_active_count = 0

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> TaskExecutionResult:
        _ = role_id
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        self.calls.append(task.task_id)
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        await asyncio.sleep(0.03)
        self.active_count -= 1
        ended_at = loop.time()
        self.spans[task.task_id] = (started_at, ended_at)
        result = f"{task.task_id} done"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return TaskExecutionResult(output=result)


class _CancellingTaskExecutionService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> TaskExecutionResult:
        _ = instance_id, role_id
        self.calls.append(task.task_id)
        raise asyncio.CancelledError


class _CapturingHookService:
    def __init__(
        self,
        decision: HookDecisionType = HookDecisionType.ALLOW,
        *,
        reason: str = "",
    ) -> None:
        self.calls: list[tuple[object, object | None]] = []
        self._decision = decision
        self._reason = reason

    async def execute(
        self,
        *,
        event_input: object,
        run_event_hub: object | None,
    ) -> HookDecisionBundle:
        self.calls.append((event_input, run_event_hub))
        return HookDecisionBundle(decision=self._decision, reason=self._reason)


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
                "orch_create_tasks",
                "orch_update_task",
                "orch_dispatch_task",
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
async def test_verification_context_uses_runtime_effective_role(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _, task_execution_service = _build_coordinator(tmp_path)
    runtime_role_resolver = RuntimeRoleResolver(
        role_registry=RoleRegistry(),
        temporary_role_repository=TemporaryRoleRepository(
            tmp_path / "verification_runtime_roles.db"
        ),
    )
    _ = runtime_role_resolver.create_temporary_role(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="runtime_only",
            name="Runtime Only",
            description="Runtime-scoped verifier.",
            tools=("shell",),
            system_prompt="Verify work.",
        ),
    )
    task_execution_service.runtime_role_resolver = runtime_role_resolver
    task = TaskEnvelope(
        task_id="task-runtime-role",
        session_id="session-1",
        trace_id="run-1",
        role_id="runtime_only",
        objective="verify work",
        verification=VerificationPlan(),
    )

    allowed_tools, workspace_root = await coordinator._verification_context(
        task=task,
        instance_id=None,
    )

    assert "shell" in allowed_tools
    assert workspace_root is None


@pytest.mark.asyncio
async def test_verification_context_denies_tools_when_role_lookup_fails(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _, task_execution_service = _build_coordinator(tmp_path)
    task_execution_service.runtime_role_resolver = RuntimeRoleResolver(
        role_registry=RoleRegistry(),
        temporary_role_repository=TemporaryRoleRepository(
            tmp_path / "verification_missing_runtime_roles.db"
        ),
    )
    task = TaskEnvelope(
        task_id="task-missing-runtime-role",
        session_id="session-1",
        trace_id="run-1",
        role_id="missing_runtime",
        objective="verify work",
        verification=VerificationPlan(),
    )

    allowed_tools, workspace_root = await coordinator._verification_context(
        task=task,
        instance_id=None,
    )

    assert allowed_tools == ()
    assert workspace_root is None


@pytest.mark.asyncio
async def test_verification_context_uses_no_workspace_when_instance_lookup_fails(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _, task_execution_service = _build_coordinator(tmp_path)
    task_execution_service.workspace_manager = WorkspaceManager(project_root=tmp_path)
    task = TaskEnvelope(
        task_id="task-missing-instance",
        session_id="session-1",
        trace_id="run-1",
        role_id="time",
        objective="verify work",
        verification=VerificationPlan(),
    )

    _, workspace_root = await coordinator._verification_context(
        task=task,
        instance_id="missing-instance",
    )

    assert workspace_root is None


@pytest.mark.asyncio
async def test_verification_tool_policy_falls_back_when_intent_lookup_fails(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _, task_execution_service = _build_coordinator(tmp_path)
    task_execution_service.run_intent_repo = _FailingRunIntentRepository(
        tmp_path / "failing_intent.db"
    )

    policy = await coordinator._verification_tool_policy_async(
        trace_id="run-1",
        fallback_session_id="session-1",
    )

    assert policy.yolo is False


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
    coordinator = CoordinatorGraph.model_construct(
        task_repo=task_repo,
        event_bus=event_log,
    )

    result = await coordinator._terminal_status_from_verification_async(
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

    events = event_log.list_by_session("session-1")
    assert events == ()


@pytest.mark.asyncio
async def test_root_task_created_hook_is_not_emitted_for_normal_run(
    tmp_path: Path,
) -> None:
    hook_service = _CapturingHookService()
    coordinator = CoordinatorGraph.model_construct(
        hook_service=cast(HookService, hook_service),
        run_event_hub=RunEventHub(),
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    await coordinator._execute_task_created_hooks(root_task=root_task)

    assert hook_service.calls == []


@pytest.mark.asyncio
async def test_root_task_created_hook_denial_blocks_delegated_root_task(
    tmp_path: Path,
) -> None:
    hook_service = _CapturingHookService(
        HookDecisionType.DENY,
        reason="blocked by policy",
    )
    run_event_hub = RunEventHub()
    coordinator = CoordinatorGraph.model_construct(
        hook_service=cast(HookService, hook_service),
        run_event_hub=run_event_hub,
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id="parent-task-1",
        trace_id="run-1",
        role_id="Coordinator",
        title="Delegated root",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    with pytest.raises(ValueError, match="blocked by policy"):
        await coordinator._execute_task_created_hooks(root_task=root_task)

    assert len(hook_service.calls) == 1
    event_input, captured_run_event_hub = hook_service.calls[0]
    assert captured_run_event_hub is run_event_hub
    assert isinstance(event_input, TaskCreatedInput)
    assert event_input.created_task_id == "task-root-1"
    assert event_input.parent_task_id == "parent-task-1"


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


@pytest.mark.asyncio
async def test_pending_delegated_tasks_run_parallel_by_instance_lane(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    slow_execution_service = _SlowRecordingTaskExecutionService(task_repo)
    coordinator.task_execution_service = cast(
        TaskExecutionService,
        slow_execution_service,
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    first_lane_task = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query first time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    second_lane_task = TaskEnvelope(
        task_id="task-child-2",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query second time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    first_lane_followup = TaskEnvelope(
        task_id="task-child-3",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query third time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    for task in (root_task, first_lane_task, second_lane_task, first_lane_followup):
        _ = task_repo.create(task)

    first_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time-1",
    )
    second_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time-2",
    )
    for instance in (first_instance, second_instance):
        agent_repo.upsert_instance(
            run_id="run-1",
            trace_id="run-1",
            session_id="session-1",
            instance_id=instance.instance_id,
            role_id="time",
            workspace_id=instance.workspace_id,
            conversation_id=instance.conversation_id,
            status=InstanceStatus.IDLE,
        )
    task_repo.update_status(
        first_lane_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=first_instance.instance_id,
    )
    task_repo.update_status(
        second_lane_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=second_instance.instance_id,
    )
    task_repo.update_status(
        first_lane_followup.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=first_instance.instance_id,
    )

    ran_any = await coordinator._run_pending_delegated_tasks(
        trace_id="run-1",
        root_task_id=root_task.task_id,
    )

    assert ran_any is True
    assert slow_execution_service.max_active_count == 2
    assert set(slow_execution_service.calls) == {
        first_lane_task.task_id,
        second_lane_task.task_id,
        first_lane_followup.task_id,
    }
    first_start, first_end = slow_execution_service.spans[first_lane_task.task_id]
    followup_start, _followup_end = slow_execution_service.spans[
        first_lane_followup.task_id
    ]
    second_start, second_end = slow_execution_service.spans[second_lane_task.task_id]
    assert followup_start >= first_end
    assert second_start < first_end
    assert first_start < second_end


@pytest.mark.asyncio
async def test_pending_delegated_task_cancellation_exits_lane_when_stop_requested(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    cancelling_service = _CancellingTaskExecutionService()
    coordinator.task_execution_service = cast(TaskExecutionService, cancelling_service)
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    child_task = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    queued_task = TaskEnvelope(
        task_id="task-child-2",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query later",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(child_task)
    _ = task_repo.create(queued_task)
    child_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=child_instance.instance_id,
        role_id="time",
        workspace_id=child_instance.workspace_id,
        conversation_id=child_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    task_repo.update_status(
        child_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=child_instance.instance_id,
    )
    task_repo.update_status(
        queued_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=child_instance.instance_id,
    )
    _ = coordinator.run_control_manager.request_subagent_stop(
        run_id="run-1",
        instance_id=child_instance.instance_id,
    )

    ran_any = await coordinator._run_pending_delegated_tasks(
        trace_id="run-1",
        root_task_id=root_task.task_id,
    )

    assert ran_any is False
    assert cancelling_service.calls == [child_task.task_id]
    assert task_repo.get(queued_task.task_id).status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_pending_delegated_task_cancellation_raises_without_stop_request(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    coordinator.task_execution_service = cast(
        TaskExecutionService,
        _CancellingTaskExecutionService(),
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="do work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    child_task = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="query time",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(child_task)
    child_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=child_instance.instance_id,
        role_id="time",
        workspace_id=child_instance.workspace_id,
        conversation_id=child_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    task_repo.update_status(
        child_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=child_instance.instance_id,
    )

    with pytest.raises(asyncio.CancelledError):
        await coordinator._run_pending_delegated_tasks(
            trace_id="run-1",
            root_task_id=root_task.task_id,
        )


@pytest.mark.parametrize(
    ("phase", "runtime_status"),
    [
        (RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP, RunRuntimeStatus.STOPPED),
        (RunRuntimePhase.AWAITING_MANUAL_ACTION, RunRuntimeStatus.PAUSED),
        (RunRuntimePhase.AWAITING_RECOVERY, RunRuntimeStatus.PAUSED),
    ],
)
@pytest.mark.asyncio
async def test_prepare_recovery_preserves_paused_subagent_state(
    tmp_path: Path,
    phase: RunRuntimePhase,
    runtime_status: RunRuntimeStatus,
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
            status=runtime_status,
            phase=phase,
            active_task_id=child_task.task_id,
            active_role_id="time",
            active_subagent_instance_id=child_instance.instance_id,
            last_error="Subagent stopped by user",
        )
    )

    await coordinator._prepare_recovery_async(
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
        await coordinator._has_resumable_delegated_work_async(
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
