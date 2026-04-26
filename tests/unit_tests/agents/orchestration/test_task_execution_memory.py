# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from relay_teams.agents.orchestration.task_execution_service import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    _truncate_task_memory_result,
)
from relay_teams.agents.tasks.events import EventType
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.skills.skill_models import SkillInstructionEntry
from relay_teams.workspace import WorkspaceHandle


class _FailingSharedStore:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def manage_state(self, mutation: StateMutation) -> None:
        self.keys.append(mutation.key)
        raise RuntimeError("write failed")


def _task_envelope(
    *,
    task_id: str,
    trace_id: str = "run-1",
    session_id: str = "session-1",
    parent_task_id: str | None = "task-root",
    role_id: str = "writer",
    objective: str = "write the result",
) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id,
        session_id=session_id,
        parent_task_id=parent_task_id,
        trace_id=trace_id,
        role_id=role_id,
        objective=objective,
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )


def test_truncate_task_memory_result_limits_long_normalized_results() -> None:
    result = "alpha\n" + ("x" * (TASK_MEMORY_RESULT_EXCERPT_CHARS + 10))

    truncated = _truncate_task_memory_result(result)

    assert len(truncated) == TASK_MEMORY_RESULT_EXCERPT_CHARS + 3
    assert truncated.endswith("...")
    assert "\n" not in truncated


def test_record_memory_if_needed_does_not_fail_completed_task_on_store_error() -> None:
    shared_store = _FailingSharedStore()
    service = TaskExecutionService.model_construct(
        shared_store=cast(SharedStateRepository, shared_store)
    )
    task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write the result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )

    service._record_memory_if_needed(
        role_id="writer",
        workspace_id="workspace-1",
        task=task,
        conversation_id="conversation-1",
        instance_id="inst-1",
        lifecycle="ephemeral",
        result="completed result",
    )

    assert shared_store.keys == ["task_result:task-1"]


def test_mark_runtime_idle_after_success_preserves_other_running_lane(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_runtime_lane.db"
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    service = TaskExecutionService.model_construct(
        task_repo=task_repo,
        run_runtime_repo=run_runtime_repo,
    )
    completed_task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write first result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    running_task = TaskEnvelope(
        task_id="task-2",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write second result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(completed_task)
    _ = task_repo.create(running_task)
    task_repo.update_status(
        completed_task.task_id,
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-completed",
    )
    task_repo.update_status(
        running_task.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id="inst-running",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id="task-root",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-completed",
            active_task_id=completed_task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-completed",
        )
    )

    service._mark_runtime_idle_after_success(
        run_id="run-1",
        completed_task_id=completed_task.task_id,
    )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING
    assert runtime.active_instance_id == "inst-running"
    assert runtime.active_task_id == running_task.task_id
    assert runtime.active_role_id == "writer"
    assert runtime.active_subagent_instance_id == "inst-running"


def test_mark_runtime_idle_after_success_restores_paused_lane(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_runtime_paused_lane.db"
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    run_control_manager = RunControlManager()
    service = TaskExecutionService.model_construct(
        task_repo=task_repo,
        run_runtime_repo=run_runtime_repo,
        run_control_manager=run_control_manager,
    )
    completed_task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write first result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    paused_task = TaskEnvelope(
        task_id="task-2",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="reviewer",
        objective="review second result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(completed_task)
    _ = task_repo.create(paused_task)
    task_repo.update_status(
        completed_task.task_id,
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-completed",
    )
    task_repo.update_status(
        paused_task.task_id,
        TaskStatus.STOPPED,
        assigned_instance_id="inst-paused",
        error_message="Task stopped by user",
    )
    run_control_manager.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-paused",
        role_id="reviewer",
        task_id=paused_task.task_id,
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id="task-root",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-completed",
            active_task_id=completed_task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-completed",
        )
    )

    service._mark_runtime_idle_after_success(
        run_id="run-1",
        completed_task_id=completed_task.task_id,
    )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
    assert runtime.active_instance_id is None
    assert runtime.active_task_id == paused_task.task_id
    assert runtime.active_role_id == "reviewer"
    assert runtime.active_subagent_instance_id == "inst-paused"
    assert runtime.last_error == "Task stopped by user"


def test_mark_runtime_idle_after_success_prefers_subagent_over_coordinator(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_runtime_subagent_priority.db"
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    service = TaskExecutionService.model_construct(
        task_repo=task_repo,
        run_runtime_repo=run_runtime_repo,
    )
    coordinator_task = TaskEnvelope(
        task_id="task-root",
        session_id="session-1",
        parent_task_id=None,
        trace_id="run-1",
        role_id="Coordinator",
        objective="coordinate delegated work",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    completed_task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id=coordinator_task.task_id,
        trace_id="run-1",
        role_id="writer",
        objective="write first result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    running_task = TaskEnvelope(
        task_id="task-2",
        session_id="session-1",
        parent_task_id=coordinator_task.task_id,
        trace_id="run-1",
        role_id="researcher",
        objective="research second result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(coordinator_task)
    _ = task_repo.create(completed_task)
    _ = task_repo.create(running_task)
    task_repo.update_status(
        coordinator_task.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id="inst-coordinator",
    )
    task_repo.update_status(
        completed_task.task_id,
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-completed",
    )
    task_repo.update_status(
        running_task.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id="inst-running-subagent",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id=coordinator_task.task_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-completed",
            active_task_id=completed_task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-completed",
        )
    )

    service._mark_runtime_idle_after_success(
        run_id="run-1",
        completed_task_id=completed_task.task_id,
    )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING
    assert runtime.active_instance_id == "inst-running-subagent"
    assert runtime.active_task_id == running_task.task_id
    assert runtime.active_role_id == "researcher"
    assert runtime.active_subagent_instance_id == "inst-running-subagent"


def test_mark_runtime_after_terminal_failure_promotes_other_running_lane(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_runtime_failure_lane.db"
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    service = TaskExecutionService.model_construct(
        task_repo=task_repo,
        run_runtime_repo=run_runtime_repo,
    )
    failed_task = TaskEnvelope(
        task_id="task-1",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write first result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    running_task = TaskEnvelope(
        task_id="task-2",
        session_id="session-1",
        parent_task_id="task-root",
        trace_id="run-1",
        role_id="writer",
        objective="write second result",
        verification=VerificationPlan(checklist=("non_empty_response",)),
    )
    _ = task_repo.create(failed_task)
    _ = task_repo.create(running_task)
    task_repo.update_status(
        failed_task.task_id,
        TaskStatus.FAILED,
        assigned_instance_id="inst-failed",
        error_message="model failed",
    )
    task_repo.update_status(
        running_task.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id="inst-running",
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id="task-root",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-failed",
            active_task_id=failed_task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-failed",
        )
    )

    service._mark_runtime_after_terminal_task_update(
        run_id="run-1",
        terminal_task_id=failed_task.task_id,
        status=RunRuntimeStatus.FAILED,
        phase=RunRuntimePhase.IDLE,
        active_instance_id=None,
        active_task_id=None,
        active_role_id=None,
        active_subagent_instance_id=None,
        last_error="model failed",
    )

    runtime = run_runtime_repo.get("run-1")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.RUNNING
    assert runtime.phase == RunRuntimePhase.SUBAGENT_RUNNING
    assert runtime.active_instance_id == "inst-running"
    assert runtime.active_task_id == running_task.task_id
    assert runtime.active_role_id == "writer"
    assert runtime.active_subagent_instance_id == "inst-running"
    assert runtime.last_error == "model failed"


def test_complete_with_assistant_error_persists_failure_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_assistant_error.db"
    message_repo = MessageRepository(db_path)
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    event_bus = EventLog(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    service = TaskExecutionService.model_construct(
        message_repo=message_repo,
        task_repo=task_repo,
        agent_repo=agent_repo,
        event_bus=event_bus,
        shared_store=SharedStateRepository(tmp_path / "task_execution_state.db"),
        run_runtime_repo=run_runtime_repo,
        run_event_hub=None,
        run_control_manager=RunControlManager(),
        hook_service=None,
    )
    task = _task_envelope(task_id="task-1")
    _ = task_repo.create(task)
    agent_repo.upsert_instance(
        run_id=task.trace_id,
        trace_id=task.trace_id,
        session_id=task.session_id,
        instance_id="inst-1",
        role_id="writer",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.RUNNING,
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id=task.trace_id,
            session_id=task.session_id,
            root_task_id="task-root",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-1",
            active_task_id=task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-1",
        )
    )

    result = service._complete_with_assistant_error(
        task=task,
        instance_id="inst-1",
        role_id="writer",
        conversation_id="conversation-1",
        workspace_id="workspace-1",
        assistant_message="assistant failed",
        error_code="provider_error",
        error_message="provider rejected request",
    )

    failed_record = task_repo.get(task.task_id)
    instance = agent_repo.get_instance("inst-1")
    runtime = run_runtime_repo.get(task.trace_id)
    events = event_bus.list_by_trace(task.trace_id)
    assert result.output == "assistant failed"
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "provider_error"
    assert failed_record.status == TaskStatus.FAILED
    assert failed_record.result == "assistant failed"
    assert failed_record.error_message == "provider rejected request"
    assert instance.status == InstanceStatus.FAILED
    assert runtime is not None
    assert runtime.phase == RunRuntimePhase.IDLE
    assert runtime.active_task_id is None
    assert runtime.last_error == "provider rejected request"
    assert len(message_repo.get_history_for_conversation("conversation-1")) == 1
    assert len(events) == 1
    assert events[0]["event_type"] == EventType.TASK_FAILED.value


@pytest.mark.asyncio
async def test_promote_paused_runtime_lane_async_restores_paused_lane(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "task_execution_runtime_paused_lane_async.db"
    task_repo = TaskRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    run_control_manager = RunControlManager()
    service = TaskExecutionService.model_construct(
        task_repo=task_repo,
        run_runtime_repo=run_runtime_repo,
        run_control_manager=run_control_manager,
    )
    completed_task = _task_envelope(task_id="task-1")
    paused_task = _task_envelope(
        task_id="task-2",
        role_id="reviewer",
        objective="review second result",
    )
    _ = task_repo.create(completed_task)
    _ = task_repo.create(paused_task)
    task_repo.update_status(
        completed_task.task_id,
        TaskStatus.COMPLETED,
        assigned_instance_id="inst-completed",
    )
    task_repo.update_status(
        paused_task.task_id,
        TaskStatus.STOPPED,
        assigned_instance_id="inst-paused",
        error_message="Task stopped by user",
    )
    run_control_manager.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-paused",
        role_id="reviewer",
        task_id=paused_task.task_id,
    )
    run_runtime_repo.upsert(
        RunRuntimeRecord(
            run_id="run-1",
            session_id="session-1",
            root_task_id="task-root",
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id="inst-completed",
            active_task_id=completed_task.task_id,
            active_role_id="writer",
            active_subagent_instance_id="inst-completed",
        )
    )

    promoted = await service._promote_paused_runtime_lane_async(
        run_id="run-1",
        terminal_task_id=completed_task.task_id,
        last_error=None,
    )

    runtime = run_runtime_repo.get("run-1")
    assert promoted is True
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP
    assert runtime.active_instance_id is None
    assert runtime.active_task_id == paused_task.task_id
    assert runtime.active_role_id == "reviewer"
    assert runtime.active_subagent_instance_id == "inst-paused"
    assert runtime.last_error == "Task stopped by user"


def test_prompt_tool_and_state_compatibility_helpers(
    tmp_path: Path,
) -> None:
    shared_store = SharedStateRepository(tmp_path / "task_execution_state.db")
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1"),
            key="session-key",
            value_json='"session-value"',
        )
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.ROLE, scope_id="session-1:writer"),
            key="role-key",
            value_json='"role-value"',
        )
    )
    service = TaskExecutionService.model_construct(
        shared_store=shared_store,
        skill_runtime_service=None,
    )
    role = RoleDefinition(
        role_id="writer",
        name="Writer",
        description="Writes concise responses",
        version="1",
        system_prompt="Write concise responses.",
    )
    task = _task_envelope(task_id="task-1")

    snapshot = service._shared_state_snapshot(
        session_id="session-1",
        role_id="writer",
        conversation_id="conversation-1",
    )
    tool_entry = TaskExecutionService._tool_entry_from_definition(
        source="local",
        name="read_file",
        description="Read a file",
        kind="function",
        strict=True,
        sequential=False,
        parameters_json_schema={"type": "object"},
    )
    user_prompt, skill_instructions = service._build_user_prompt(
        role=role,
        objective="Draft summary",
        shared_state_snapshot=snapshot,
        conversation_context=None,
        orchestration_prompt="",
    )
    prompt_skill_instructions = TaskExecutionService._to_prompt_skill_instructions(
        (
            SkillInstructionEntry(
                name="search",
                description="Find relevant context",
            ),
        )
    )
    merged_prompt = TaskExecutionService._merge_provider_prompt_content(
        provider_content="Original prompt",
        user_prompt_text="Draft summary\n\n## Skill Candidates\n- search",
    )

    assert ("session-key", '"session-value"') in snapshot
    assert ("role-key", '"role-value"') in snapshot
    assert tool_entry.name == "read_file"
    assert tool_entry.parameters_json_schema == {"type": "object"}
    assert TaskExecutionService._normalize_tool_kind("output") == "output"
    assert TaskExecutionService._normalize_tool_kind("external") == "external"
    assert TaskExecutionService._normalize_tool_kind("unapproved") == "unapproved"
    assert TaskExecutionService._normalize_tool_kind("custom-kind") == "function"
    assert "Draft summary" in user_prompt
    assert skill_instructions == ()
    assert prompt_skill_instructions[0].name == "search"
    assert (
        TaskExecutionService._user_prompt_skill_appendix(
            "Draft summary\n\n## Skill Candidates\n- search"
        )
        == "## Skill Candidates\n- search"
    )
    assert merged_prompt == "Original prompt\n\n## Skill Candidates\n- search"
    assert (
        TaskExecutionService._resolve_turn_objective(
            task=task,
            user_prompt_override=" Override objective ",
        )
        == "Override objective"
    )


def test_completion_guard_wrappers_default_to_no_issue() -> None:
    service = TaskExecutionService.model_construct(
        run_intent_repo=None,
        todo_service=None,
        reminder_service=None,
    )
    task = _task_envelope(task_id="task-1", parent_task_id=None)
    decision = service._evaluate_completion_guard(
        task=task,
        instance_id="inst-1",
        role_id="writer",
        workspace=cast(WorkspaceHandle, object()),
        conversation_id="conversation-1",
        output_text="done",
    )
    thinking = service._thinking_for_run("missing-run")

    assert decision.issue is False
    assert thinking.enabled is False


@pytest.mark.asyncio
async def test_completion_guard_async_wrapper_defaults_to_no_issue() -> None:
    service = TaskExecutionService.model_construct(
        run_intent_repo=None,
        todo_service=None,
        reminder_service=None,
    )
    task = _task_envelope(task_id="task-1", parent_task_id=None)

    decision = await service._evaluate_completion_guard_async(
        task=task,
        instance_id="inst-1",
        role_id="writer",
        workspace=cast(WorkspaceHandle, object()),
        conversation_id="conversation-1",
        output_text="done",
    )

    assert decision.issue is False
