# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from relay_teams.agents.orchestration.task_execution_service import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    _truncate_task_memory_result,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.persistence.scope_models import StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.agents.tasks.task_repository import TaskRepository


class _FailingSharedStore:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def manage_state(self, mutation: StateMutation) -> None:
        self.keys.append(mutation.key)
        raise RuntimeError("write failed")


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
