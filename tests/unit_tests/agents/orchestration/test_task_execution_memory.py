# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import cast

from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionService,
)
from relay_teams.agents.orchestration.task_execution_service import (
    TASK_MEMORY_RESULT_EXCERPT_CHARS,
    _truncate_task_memory_result,
)
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.persistence.scope_models import StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository


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
