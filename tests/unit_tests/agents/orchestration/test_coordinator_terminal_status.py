from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

import pytest

from relay_teams.media import content_parts_from_text
from relay_teams.agent_runtimes.instances.enums import InstanceStatus
from relay_teams.agent_runtimes.instances.models import create_subagent_instance
from relay_teams.agents.orchestration.coordinator import CoordinatorGraph
from relay_teams.agents.orchestration.delegation_planning import (
    AUTO_LANE_NODE_PREFIX,
    DelegationPlan,
    DelegationPlanningService,
)
from relay_teams.agents.orchestration.graph_models import (
    OrchestrationGraph,
    OrchestrationGraphEdge,
    OrchestrationGraphNode,
)
from relay_teams.agents.orchestration.policy_models import OrchestrationPolicy
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.agents.execution.system_prompts import RuntimePromptBuilder
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractPostcondition,
    RoleContractPostconditionType,
)
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
from relay_teams.sessions.runs.run_models import RunTopologySnapshot
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
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
from relay_teams.sessions.session_models import SessionMode
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.events import EventType
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
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.agents.orchestration.coordinator import (
    _clean_check_display_name,
    _format_verification_failure,
)
from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.agents.tasks.models import (
    VerificationCheckResult,
    VerificationReport,
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


class _FailingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[str] = []

    async def execute(
        self, *, instance_id: str, role_id: str, task: TaskEnvelope
    ) -> TaskExecutionResult:
        _ = role_id
        self.calls.append(task.task_id)
        error_message = "graph node failed"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.FAILED,
            assigned_instance_id=instance_id,
            error_message=error_message,
        )
        return TaskExecutionResult(
            output="",
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code="node_failed",
            error_message=error_message,
        )


def test_clean_check_display_name_strips_uuid_prefix() -> None:
    name = "c6d16534-7273-4ce0-b4a4-e4b58338d28f:contract_postcondition:result_mentions_acceptance:item"
    assert (
        _clean_check_display_name(name)
        == "contract_postcondition:result_mentions_acceptance:item"
    )


def test_clean_check_display_name_preserves_name_without_uuid() -> None:
    name = "contract_postcondition:result_mentions_evidence:pytest output"
    assert _clean_check_display_name(name) == name


def test_format_verification_failure_without_report() -> None:
    verification = VerificationResult(
        task_id="task-1",
        passed=False,
        details=("Task not completed yet",),
    )
    message = _format_verification_failure(verification)
    assert "Verification failed." in message
    assert "Task not completed yet" in message
    assert "Review the task spec" in message


def test_format_verification_failure_without_report_no_details() -> None:
    verification = VerificationResult(
        task_id="task-1",
        passed=False,
        details=(),
    )
    message = _format_verification_failure(verification)
    assert "Verification failed." in message
    assert "Review the task spec" in message


def test_format_verification_failure_with_report_groups_checks() -> None:
    checks = (
        VerificationCheckResult(
            layer=VerificationLayer.CONTRACT,
            name="contract_postcondition:result_mentions_acceptance:file exists",
            passed=True,
            details="Acceptance item was cited in the result.",
        ),
        VerificationCheckResult(
            layer=VerificationLayer.CONTRACT,
            name="contract_postcondition:result_mentions_evidence:pytest output",
            passed=False,
            details="Evidence item was not cited in the result.",
        ),
        VerificationCheckResult(
            layer=VerificationLayer.CONTRACT,
            name=(
                "c6d16534-7273-4ce0-b4a4-e4b58338d28f:"
                "contract_postcondition:result_mentions_acceptance:word count"
            ),
            passed=False,
            details="Acceptance item was not cited in the result.",
        ),
    )
    report = VerificationReport(
        task_id="task-1",
        passed=False,
        checks=checks,
        unmet_items=(checks[1].name, checks[2].name),
    )
    verification = VerificationResult(
        task_id="task-1",
        passed=False,
        details=(checks[1].name, checks[2].name),
        report=report,
    )
    message = _format_verification_failure(verification)
    assert "3 check(s): 1 passed, 2 failed." in message
    assert "[PASS]" in message
    assert "[FAIL]" in message
    assert "result_mentions_acceptance:file exists" in message
    assert "contract_postcondition:result_mentions_evidence:pytest output" in message
    assert "contract_postcondition:result_mentions_acceptance:word count" in message
    assert "c6d16534-7273-4ce0-b4a4-e4b58338d28f:" not in message
    assert "Review the task spec" in message


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


class _CreatingPlanningService:
    def __init__(
        self,
        *,
        task_repo: TaskRepository,
        agent_repo: AgentInstanceRepository,
        plan: DelegationPlan | None,
    ) -> None:
        self._task_repo = task_repo
        self._agent_repo = agent_repo
        self._plan = plan
        self.calls = 0

    async def plan_and_create_tasks_async(
        self,
        *,
        root_task: TaskEnvelope,
        topology: RunTopologySnapshot | None,
        policy: OrchestrationPolicy,
    ) -> DelegationPlan | None:
        _ = topology, policy
        self.calls += 1
        if self._plan is None or not self._plan.should_decompose:
            return self._plan
        for lane in self._plan.lanes:
            instance = create_subagent_instance(
                lane.role_id,
                workspace_id="workspace-1",
                conversation_id=f"conversation-{lane.lane_id}",
            )
            self._agent_repo.upsert_instance(
                run_id=root_task.trace_id,
                trace_id=root_task.trace_id,
                session_id=root_task.session_id,
                instance_id=instance.instance_id,
                role_id=lane.role_id,
                workspace_id=instance.workspace_id,
                conversation_id=instance.conversation_id,
                status=InstanceStatus.IDLE,
            )
            task = TaskEnvelope(
                task_id=f"task-auto-{lane.lane_id}",
                session_id=root_task.session_id,
                parent_task_id=root_task.task_id,
                trace_id=root_task.trace_id,
                role_id=lane.role_id,
                title=lane.title,
                objective=lane.objective,
                verification=VerificationPlan(checklist=("non_empty_response",)),
                orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}{lane.lane_id}",
            )
            _ = await self._task_repo.create_async(task)
            await self._task_repo.update_status_async(
                task.task_id,
                TaskStatus.ASSIGNED,
                assigned_instance_id=instance.instance_id,
            )
        return self._plan


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
async def test_verification_tool_policy_uses_persisted_run_intent(
    tmp_path: Path,
) -> None:
    coordinator, _, _, _, task_execution_service = _build_coordinator(tmp_path)
    run_intent_repo = RunIntentRepository(tmp_path / "verification_intent.db")
    task_execution_service.run_intent_repo = run_intent_repo
    await run_intent_repo.upsert_async(
        run_id="run-1",
        session_id="session-1",
        intent=IntentInput(
            session_id="session-1",
            input=content_parts_from_text("verify shell policy"),
            yolo=True,
            shell_safety_policy_enabled=False,
        ),
    )

    policy = await coordinator._verification_tool_policy_async(
        trace_id="run-1",
        fallback_session_id="session-1",
    )

    assert policy.yolo is True
    assert policy.shell_safety_policy_enabled is False


@pytest.mark.asyncio
async def test_verify_task_async_includes_delegated_role_contracts(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, _task_execution_service = (
        _build_coordinator(tmp_path)
    )
    coordinator.role_registry.register(
        RoleDefinition(
            role_id="reviewer",
            name="Reviewer",
            description="Reviews completed work.",
            version="1",
            tools=(),
            contract=RoleContract(
                postconditions=(
                    RoleContractPostcondition(
                        guarantee=(
                            RoleContractPostconditionType.RESULT_MENTIONS_EVIDENCE_EXPECTATIONS
                        )
                    ),
                )
            ),
            system_prompt="Review evidence.",
        )
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="coordinate work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    delegated_task = TaskEnvelope(
        task_id="task-review-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="reviewer",
        objective="review work",
        verification=VerificationPlan(
            checklist=("non_empty_response",),
            evidence_expectations=("pytest output",),
        ),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(delegated_task)
    task_repo.update_status(
        root_task.task_id,
        TaskStatus.COMPLETED,
        result="root task completed",
    )
    task_repo.update_status(
        delegated_task.task_id,
        TaskStatus.COMPLETED,
        result="review completed without evidence details",
    )

    verification = await coordinator._verify_task_async(
        root_task_id=root_task.task_id,
        allowed_tools=(),
        tool_approval_policy=ToolApprovalPolicy(),
        workspace_root=None,
    )

    assert verification.passed is False
    assert verification.report is not None
    assert (
        "task-review-1:contract_postcondition:result_mentions_evidence:pytest output"
        in verification.report.unmet_items
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
async def test_pending_delegated_tasks_skip_unassigned_created_task(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
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
        role_id=None,
        objective="needs assignment",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    _ = task_repo.create(child_task)

    ran_any = await coordinator._run_pending_delegated_tasks(
        trace_id="run-1",
        root_task_id=root_task.task_id,
    )

    child_record = task_repo.get(child_task.task_id)
    assert ran_any is False
    assert child_record.status == TaskStatus.CREATED
    assert child_record.error_message is None
    assert task_execution_service.calls == []


@pytest.mark.asyncio
async def test_ai_mode_respects_zero_cycle_orchestration_policy(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="answer directly",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="simple",
        orchestration_prompt="Answer simple requests directly.",
        allowed_role_ids=("time",),
        orchestration_policy=OrchestrationPolicy(
            max_orchestration_cycles=0,
            max_parallel_delegated_tasks=0,
        ),
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        coordinator_first=False,
        initial_result="direct answer",
        topology=topology,
    )

    assert result.output == "direct answer"
    assert task_execution_service.calls == []


@pytest.mark.asyncio
async def test_ai_mode_reports_zero_cycle_policy_with_pending_tasks(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="delegate then stop",
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
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="simple",
        orchestration_prompt="Do not run follow-up cycles.",
        allowed_role_ids=("time",),
        orchestration_policy=OrchestrationPolicy(
            max_orchestration_cycles=0,
            max_parallel_delegated_tasks=4,
        ),
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "orchestration_cycles_exhausted"
    assert "zero orchestration cycles" in result.output
    assert task_execution_service.calls == [root_task.task_id]
    assert task_repo.get(root_task.task_id).status == TaskStatus.FAILED
    assert task_repo.get(child_task.task_id).status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_ai_mode_reports_disabled_parallel_policy_with_pending_tasks(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="answer through a delegated task",
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
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="simple",
        orchestration_prompt="Delegation is disabled by policy.",
        allowed_role_ids=("time",),
        orchestration_policy=OrchestrationPolicy(
            max_orchestration_cycles=1,
            max_parallel_delegated_tasks=0,
        ),
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        coordinator_first=False,
        topology=topology,
    )

    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "delegated_task_execution_disabled"
    assert "Delegated task execution is disabled" in result.output
    assert task_execution_service.calls == []
    assert task_repo.get(root_task.task_id).status == TaskStatus.FAILED
    assert task_repo.get(child_task.task_id).status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_ai_mode_uses_policy_parallel_limit(tmp_path: Path) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    slow_execution_service = _SlowRecordingTaskExecutionService(task_repo)
    coordinator.task_execution_service = cast(
        TaskExecutionService, slow_execution_service
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="run limited delegated work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    first_child = TaskEnvelope(
        task_id="task-child-1",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="first child",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    second_child = TaskEnvelope(
        task_id="task-child-2",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="second child",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    for task in (root_task, first_child, second_child):
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
        first_child.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=first_instance.instance_id,
    )
    task_repo.update_status(
        second_child.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=second_instance.instance_id,
    )
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="limited",
        orchestration_prompt="Run one delegated lane at a time.",
        allowed_role_ids=("time",),
        orchestration_policy=OrchestrationPolicy(
            max_orchestration_cycles=1,
            max_parallel_delegated_tasks=1,
        ),
    )

    _ = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        coordinator_first=False,
        topology=topology,
    )

    assert slow_execution_service.max_active_count == 1
    assert first_child.task_id in slow_execution_service.calls
    assert second_child.task_id in slow_execution_service.calls
    assert root_task.task_id in slow_execution_service.calls


@pytest.mark.asyncio
async def test_graph_mode_runs_fanout_then_join_before_final_coordinator(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    slow_execution_service = _SlowRecordingTaskExecutionService(task_repo)
    coordinator.task_execution_service = cast(
        TaskExecutionService, slow_execution_service
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship the graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph.model_validate(
        {
            "max_parallel_tasks": 2,
            "nodes": [
                {"node_id": "left", "role_id": "time", "objective": "Do left work."},
                {"node_id": "right", "role_id": "time", "objective": "Do right work."},
                {
                    "node_id": "join",
                    "role_id": "time",
                    "objective": "Join both results.",
                },
            ],
            "edges": [
                {"from_node_id": "left", "to_node_id": "join"},
                {"from_node_id": "right", "to_node_id": "join"},
            ],
        }
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("time",),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }
    left_record = records_by_node["left"]
    right_record = records_by_node["right"]
    join_record = records_by_node["join"]
    left_start, left_end = slow_execution_service.spans[left_record.envelope.task_id]
    right_start, right_end = slow_execution_service.spans[right_record.envelope.task_id]
    join_start, _join_end = slow_execution_service.spans[join_record.envelope.task_id]

    assert result.output.startswith("Graph-based orchestration completed.")
    assert "join role=time" in result.output
    root_record = await task_repo.get_async(root_task.task_id)
    assert root_record.status == TaskStatus.COMPLETED
    assert root_record.result == result.output
    assert set(records_by_node) == {"left", "right", "join"}
    assert all(
        record.status == TaskStatus.COMPLETED for record in records_by_node.values()
    )
    assert root_task.task_id not in slow_execution_service.calls
    assert slow_execution_service.max_active_count == 2
    assert right_start < left_end
    assert left_start < right_end
    assert join_start >= left_end
    assert join_start >= right_end
    assert left_record.envelope.task_id in join_record.envelope.depends_on_task_ids
    assert right_record.envelope.task_id in join_record.envelope.depends_on_task_ids
    assert left_record.result is not None
    assert right_record.result is not None
    assert left_record.result in join_record.envelope.objective
    assert right_record.result in join_record.envelope.objective


@pytest.mark.asyncio
async def test_graph_mode_uses_auto_delegation_plan_before_fixed_graph(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship the graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )
    planning_service = _CreatingPlanningService(
        task_repo=task_repo,
        agent_repo=agent_repo,
        plan=DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "implementation",
                        "title": "Planned implementation",
                        "role_id": "time",
                        "objective": "Run the planner-created implementation lane.",
                    }
                ],
            }
        ),
    )
    coordinator.planning_service = cast(DelegationPlanningService, planning_service)

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }
    auto_record = records_by_node[f"{AUTO_LANE_NODE_PREFIX}implementation"]

    assert planning_service.calls == 1
    assert "implement" not in records_by_node
    assert auto_record.status == TaskStatus.COMPLETED
    assert task_execution_service.calls == [
        auto_record.envelope.task_id,
        root_task.task_id,
    ]
    assert result.output == "task-root-1 done"


@pytest.mark.asyncio
async def test_graph_mode_resume_existing_auto_lanes_uses_dynamic_cycle(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship the graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    lane_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-auto-lane",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=lane_instance.instance_id,
        role_id="time",
        workspace_id=lane_instance.workspace_id,
        conversation_id=lane_instance.conversation_id,
        status=InstanceStatus.IDLE,
    )
    auto_task = TaskEnvelope(
        task_id="task-auto-implementation",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        title="Planned implementation",
        objective="Resume the planner-created implementation lane.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}implementation",
    )
    _ = task_repo.create(auto_task)
    task_repo.update_status(
        auto_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=lane_instance.instance_id,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        coordinator_first=False,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }

    assert "implement" not in records_by_node
    assert records_by_node[f"{AUTO_LANE_NODE_PREFIX}implementation"].status == (
        TaskStatus.COMPLETED
    )
    assert task_execution_service.calls == [auto_task.task_id, root_task.task_id]
    assert result.output == "task-root-1 done"


@pytest.mark.asyncio
async def test_graph_mode_resume_terminal_auto_lanes_runs_coordinator_synthesis(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="summarize completed planner lanes",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    auto_task = TaskEnvelope(
        task_id="task-auto-implementation",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        title="Planned implementation",
        objective="Already finished planner-created work.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}implementation",
    )
    _ = task_repo.create(auto_task)
    task_repo.update_status(
        auto_task.task_id,
        TaskStatus.COMPLETED,
        result="implementation already done",
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )
    planning_service = _CreatingPlanningService(
        task_repo=task_repo,
        agent_repo=agent_repo,
        plan=DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "new_implementation",
                        "title": "Duplicate planner lane",
                        "role_id": "time",
                        "objective": "This lane must not be created on resume.",
                    }
                ],
            }
        ),
    )
    coordinator.planning_service = cast(DelegationPlanningService, planning_service)

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }

    assert planning_service.calls == 0
    assert "implement" not in records_by_node
    assert f"{AUTO_LANE_NODE_PREFIX}new_implementation" not in records_by_node
    assert task_execution_service.calls == [root_task.task_id]
    assert result.output == "task-root-1 done"


@pytest.mark.asyncio
async def test_graph_mode_resume_paused_auto_lanes_skips_coordinator_prepass(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="resume the paused planner lane",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    lane_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-auto-lane",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=lane_instance.instance_id,
        role_id="time",
        workspace_id=lane_instance.workspace_id,
        conversation_id=lane_instance.conversation_id,
        status=InstanceStatus.STOPPED,
    )
    auto_task = TaskEnvelope(
        task_id="task-auto-implementation",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        title="Planned implementation",
        objective="Wait for manual input before continuing.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}implementation",
    )
    _ = task_repo.create(auto_task)
    task_repo.update_status(
        auto_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=lane_instance.instance_id,
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
            active_task_id=auto_task.task_id,
            active_role_id="time",
            active_subagent_instance_id=lane_instance.instance_id,
            last_error="Waiting for manual action",
        )
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    root_record = await task_repo.get_async(root_task.task_id)
    auto_record = await task_repo.get_async(auto_task.task_id)
    assert result.output == ""
    assert task_execution_service.calls == []
    assert root_record.status != TaskStatus.COMPLETED
    assert auto_record.status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
async def test_graph_mode_resume_existing_fixed_nodes_skips_planner_preflight(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="resume the fixed graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    implement_task = TaskEnvelope(
        task_id="task-graph-implement",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        title="Implementation",
        objective="Already completed fixed graph implementation.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id="implement",
    )
    _ = task_repo.create(implement_task)
    task_repo.update_status(
        implement_task.task_id,
        TaskStatus.COMPLETED,
        result="implementation already done",
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
            OrchestrationGraphNode(
                node_id="verify",
                role_id="time",
                objective="Verify the resumed fixed graph work.",
            ),
        ),
        edges=(
            OrchestrationGraphEdge(
                from_node_id="implement",
                to_node_id="verify",
            ),
        ),
        final_response_node_id="verify",
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )
    planning_service = _CreatingPlanningService(
        task_repo=task_repo,
        agent_repo=agent_repo,
        plan=DelegationPlan.model_validate(
            {
                "should_decompose": True,
                "lanes": [
                    {
                        "lane_id": "implementation",
                        "title": "Planned implementation",
                        "role_id": "time",
                        "objective": "This auto lane must not be created on resume.",
                    }
                ],
            }
        ),
    )
    coordinator.planning_service = cast(DelegationPlanningService, planning_service)

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }

    assert planning_service.calls == 0
    assert f"{AUTO_LANE_NODE_PREFIX}implementation" not in records_by_node
    assert records_by_node["implement"].status == TaskStatus.COMPLETED
    assert records_by_node["verify"].status == TaskStatus.COMPLETED
    assert task_execution_service.calls == [records_by_node["verify"].envelope.task_id]
    assert result.output.startswith("Graph-based orchestration completed.")


@pytest.mark.asyncio
async def test_dynamic_cycles_report_exhausted_budget_with_pending_dependencies(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship dependent lanes",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    first_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-first-lane",
    )
    second_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-second-lane",
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
    first_task = TaskEnvelope(
        task_id="task-auto-first",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="Run the first lane.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}first",
    )
    second_task = TaskEnvelope(
        task_id="task-auto-second",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="Run the second lane after the first lane.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}second",
        depends_on_task_ids=(first_task.task_id,),
    )
    _ = task_repo.create(first_task)
    _ = task_repo.create(second_task)
    task_repo.update_status(
        first_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=first_instance.instance_id,
    )
    task_repo.update_status(
        second_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=second_instance.instance_id,
    )

    result = await coordinator._run_dynamic_delegation_cycles_async(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        policy=OrchestrationPolicy(max_orchestration_cycles=1),
        coordinator_result=TaskExecutionResult(output=""),
    )

    root_record = await task_repo.get_async(root_task.task_id)
    first_record = await task_repo.get_async(first_task.task_id)
    second_record = await task_repo.get_async(second_task.task_id)

    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "orchestration_cycles_exhausted"
    assert root_record.status == TaskStatus.FAILED
    assert first_record.status == TaskStatus.COMPLETED
    assert second_record.status == TaskStatus.ASSIGNED
    assert task_execution_service.calls == [first_task.task_id, root_task.task_id]


@pytest.mark.asyncio
async def test_dynamic_cycles_do_not_exhaust_budget_for_paused_lane(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="resume paused planner lane later",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    paused_instance = create_subagent_instance(
        "time",
        workspace_id="workspace-1",
        conversation_id="conversation-time",
    )
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id=paused_instance.instance_id,
        role_id="time",
        workspace_id=paused_instance.workspace_id,
        conversation_id=paused_instance.conversation_id,
        status=InstanceStatus.STOPPED,
    )
    paused_task = TaskEnvelope(
        task_id="task-auto-paused",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="Wait for manual input before continuing.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}paused",
    )
    downstream_task = TaskEnvelope(
        task_id="task-auto-downstream",
        session_id="session-1",
        parent_task_id=root_task.task_id,
        trace_id="run-1",
        role_id="time",
        objective="Continue after manual input is handled.",
        verification=VerificationPlan(checklist=("non_empty_response",)),
        orchestration_node_id=f"{AUTO_LANE_NODE_PREFIX}downstream",
        depends_on_task_ids=(paused_task.task_id,),
    )
    _ = task_repo.create(paused_task)
    _ = task_repo.create(downstream_task)
    task_repo.update_status(
        paused_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=paused_instance.instance_id,
    )
    task_repo.update_status(
        downstream_task.task_id,
        TaskStatus.ASSIGNED,
        assigned_instance_id=paused_instance.instance_id,
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id=root_task.task_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
            active_task_id=paused_task.task_id,
            active_role_id="time",
            active_subagent_instance_id=paused_instance.instance_id,
            last_error="Waiting for manual action",
        )
    )

    result = await coordinator._run_dynamic_delegation_cycles_async(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        policy=OrchestrationPolicy(max_orchestration_cycles=1),
        coordinator_result=TaskExecutionResult(output="waiting for resume"),
    )

    root_record = await task_repo.get_async(root_task.task_id)
    paused_record = await task_repo.get_async(paused_task.task_id)
    downstream_record = await task_repo.get_async(downstream_task.task_id)
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert result.output == "waiting for resume"
    assert root_record.status != TaskStatus.FAILED
    assert paused_record.status == TaskStatus.ASSIGNED
    assert downstream_record.status == TaskStatus.ASSIGNED
    assert task_execution_service.calls == []


@pytest.mark.asyncio
async def test_graph_mode_falls_back_to_fixed_graph_when_planner_declines(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship the graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )
    planning_service = _CreatingPlanningService(
        task_repo=task_repo,
        agent_repo=agent_repo,
        plan=DelegationPlan(should_decompose=False, rationale="simple graph"),
    )
    coordinator.planning_service = cast(DelegationPlanningService, planning_service)

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }

    assert planning_service.calls == 1
    assert "implement" in records_by_node
    assert not any(
        str(node_id).startswith(AUTO_LANE_NODE_PREFIX) for node_id in records_by_node
    )
    assert task_execution_service.calls == [
        records_by_node["implement"].envelope.task_id
    ]
    assert result.output.startswith("Graph-based orchestration completed.")


@pytest.mark.asyncio
async def test_graph_mode_falls_back_to_fixed_graph_when_planner_unavailable(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="ship the graph feature",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="implement",
                role_id="time",
                objective="Run the fixed implementation fallback.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("DelegationPlanner", "time"),
        orchestration_policy=OrchestrationPolicy(coordinator_inline_budget_steps=0),
        orchestration_graph=graph,
    )
    planning_service = _CreatingPlanningService(
        task_repo=task_repo,
        agent_repo=agent_repo,
        plan=None,
    )
    coordinator.planning_service = cast(DelegationPlanningService, planning_service)

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    records_by_node = {
        record.envelope.orchestration_node_id: record
        for record in records
        if record.envelope.orchestration_node_id is not None
    }

    assert planning_service.calls == 1
    assert "implement" in records_by_node
    assert not any(
        str(node_id).startswith(AUTO_LANE_NODE_PREFIX) for node_id in records_by_node
    )
    assert task_execution_service.calls == [
        records_by_node["implement"].envelope.task_id
    ]
    assert result.output.startswith("Graph-based orchestration completed.")


@pytest.mark.asyncio
async def test_graph_mode_runs_dependency_chain_beyond_standard_cycle_limit(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="run a deep graph",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        max_parallel_tasks=1,
        nodes=tuple(
            OrchestrationGraphNode(
                node_id=f"node{index}",
                role_id="time",
                objective=f"Run step {index}.",
            )
            for index in range(10)
        ),
        edges=tuple(
            OrchestrationGraphEdge(
                from_node_id=f"node{index}",
                to_node_id=f"node{index + 1}",
            )
            for index in range(9)
        ),
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("time",),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    graph_records = tuple(
        record
        for record in records
        if record.envelope.orchestration_node_id is not None
    )
    root_record = await task_repo.get_async(root_task.task_id)
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert root_record.status == TaskStatus.COMPLETED
    assert len(graph_records) == 10
    assert len(task_execution_service.calls) == 10
    assert all(record.status == TaskStatus.COMPLETED for record in graph_records)


@pytest.mark.asyncio
async def test_graph_mode_reports_disabled_parallel_policy_with_pending_tasks(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, task_execution_service = (
        _build_coordinator(tmp_path)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="run a graph with disabled delegated execution",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="blocked",
                role_id="time",
                objective="This node cannot run under the policy.",
            ),
        ),
        edges=(),
        max_parallel_tasks=4,
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph-disabled",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("time",),
        orchestration_policy=OrchestrationPolicy(
            max_orchestration_cycles=8,
            max_parallel_delegated_tasks=0,
        ),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    graph_records = tuple(
        record
        for record in records
        if record.envelope.orchestration_node_id is not None
    )
    root_record = await task_repo.get_async(root_task.task_id)
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "delegated_task_execution_disabled"
    assert "Delegated task execution is disabled" in result.output
    assert root_record.status == TaskStatus.FAILED
    assert root_record.error_message == result.error_message
    assert len(graph_records) == 1
    assert graph_records[0].status == TaskStatus.ASSIGNED
    assert task_execution_service.calls == []


@pytest.mark.asyncio
async def test_graph_mode_reports_missing_node_role_without_creating_tasks(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="run graph with stale role",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="stale",
                role_id="missingrole",
                objective="Use a removed role.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("missingrole",),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    records = await task_repo.list_by_trace_async("run-1")
    graph_records = tuple(
        record
        for record in records
        if record.envelope.orchestration_node_id is not None
    )
    root_record = await task_repo.get_async(root_task.task_id)
    events = coordinator.event_bus.list_by_trace("run-1")
    failed_events = tuple(
        event
        for event in events
        if event["event_type"] == EventType.TASK_FAILED.value
        and event["task_id"] == root_task.task_id
    )
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "graph_role_missing"
    assert result.error_message == "Graph references missing role(s): missingrole."
    assert "Missing graph node roles: missingrole" in result.output
    assert root_record.status == TaskStatus.FAILED
    assert root_record.assigned_instance_id == coordinator_instance_id
    assert root_record.error_message == result.error_message
    assert len(failed_events) == 1
    assert failed_events[0]["instance_id"] == coordinator_instance_id
    assert '"reason": "graph_role_missing"' in str(failed_events[0]["payload_json"])
    assert graph_records == ()


@pytest.mark.asyncio
async def test_graph_mode_reports_failed_node_before_blocked_status(
    tmp_path: Path,
) -> None:
    coordinator, task_repo, _agent_repo, _run_runtime_repo, _ = _build_coordinator(
        tmp_path
    )
    coordinator.task_execution_service = cast(
        TaskExecutionService, _FailingTaskExecutionService(task_repo)
    )
    root_task = TaskEnvelope(
        task_id="task-root-1",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="run a graph with a failing node",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(root_task)
    coordinator_instance_id = await coordinator._ensure_root_instance_async(
        session_id="session-1",
        trace_id="run-1",
        root_task=root_task,
        reuse_existing_instance=False,
    )
    graph = OrchestrationGraph(
        nodes=(
            OrchestrationGraphNode(
                node_id="fail",
                role_id="time",
                objective="Fail this node.",
            ),
        )
    )
    topology = RunTopologySnapshot(
        session_mode=SessionMode.ORCHESTRATION,
        main_agent_role_id="Coordinator",
        normal_root_role_id="time",
        coordinator_role_id="Coordinator",
        orchestration_preset_id="graph",
        orchestration_prompt="Run graph.",
        allowed_role_ids=("time",),
        orchestration_graph=graph,
    )

    result = await coordinator._run_ai_mode(
        trace_id="run-1",
        root_task=root_task,
        coordinator_instance_id=coordinator_instance_id,
        topology=topology,
    )

    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "graph_execution_failed"
    assert result.error_message == "One or more graph nodes failed."
    assert "status=failed" in result.output
    assert "graph node failed" in result.output


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
