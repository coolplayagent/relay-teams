# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import UserPromptPart

from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskDraft,
    TaskOrchestrationService,
    TaskUpdate,
)
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry

from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.orchestration.task_execution_service import TaskExecutionService
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _FakeTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[tuple[str, str, str]] = []

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
        _ = user_prompt_override
        self.calls.append((instance_id, role_id, task.task_id))
        result = f"done:{task.task_id}"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return result


def _build_role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="coordinator_agent",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=(),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            description="Implements requested changes.",
            version="1.0.0",
            tools=(),
            system_prompt="Implement code.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="reviewer",
            name="Reviewer",
            description="Reviews completed changes.",
            version="1.0.0",
            tools=(),
            system_prompt="Review code.",
        )
    )
    return registry


def _seed_root_task(task_repo: TaskRepository) -> None:
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            role_id="coordinator_agent",
            title="Coordinator root",
            objective="Handle user intent",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def _build_service(
    db_path: Path,
) -> tuple[
    TaskOrchestrationService,
    TaskRepository,
    AgentInstanceRepository,
    MessageRepository,
    _FakeTaskExecutionService,
]:
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    session_repo = SessionRepository(db_path)
    execution_service = _FakeTaskExecutionService(task_repo)
    _seed_root_task(task_repo)
    _ = session_repo.create(session_id="session-1", workspace_id="default")
    service = TaskOrchestrationService(
        task_repo=task_repo,
        role_registry=_build_role_registry(),
        agent_repo=agent_repo,
        task_execution_service=cast(TaskExecutionService, execution_service),
        message_repo=message_repo,
        session_repo=session_repo,
    )
    return service, task_repo, agent_repo, message_repo, execution_service


@pytest.mark.asyncio
async def test_create_tasks_auto_dispatch_binds_new_instance(tmp_path: Path) -> None:
    (
        service,
        task_repo,
        agent_repo,
        _message_repo,
        execution_service,
    ) = _build_service(tmp_path / "task_orchestration_create.db")

    payload = await service.create_tasks(
        run_id="run-1",
        tasks=[
            TaskDraft(
                role_id="spec_coder",
                objective="Implement the endpoint",
                title="Endpoint implementation",
            )
        ],
        auto_dispatch=True,
    )

    tasks_payload = cast(list[JsonValue], payload["tasks"])
    created_task = cast(dict[str, JsonValue], tasks_payload[0])
    task_id = str(created_task["task_id"])
    record = task_repo.get(task_id)

    assert payload["ok"] is True
    assert payload["created_count"] == 1
    assert record.envelope.parent_task_id == "task-root"
    assert record.envelope.role_id == "spec_coder"
    assert record.envelope.title == "Endpoint implementation"
    assert record.status == TaskStatus.COMPLETED
    assert record.assigned_instance_id
    assert execution_service.calls == [
        (str(record.assigned_instance_id), "spec_coder", task_id)
    ]
    agent = agent_repo.get_instance(str(record.assigned_instance_id))
    assert agent.role_id == "spec_coder"


def test_update_task_allows_created_only(tmp_path: Path) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_update.db"
    )
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Initial title",
            objective="Initial objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    updated = service.update_task(
        run_id="run-1",
        task_id=created.envelope.task_id,
        update=TaskUpdate(
            role_id="reviewer",
            objective="Review the implementation",
            title="Code review",
        ),
    )
    updated_record = task_repo.get(created.envelope.task_id)

    assert updated["ok"] is True
    assert updated_record.envelope.role_id == "reviewer"
    assert updated_record.envelope.objective == "Review the implementation"
    assert updated_record.envelope.title == "Code review"

    task_repo.update_status(created.envelope.task_id, TaskStatus.ASSIGNED)
    with pytest.raises(ValueError, match="only created tasks can be updated"):
        service.update_task(
            run_id="run-1",
            task_id=created.envelope.task_id,
            update=TaskUpdate(title="Should fail"),
        )


@pytest.mark.asyncio
async def test_dispatch_task_reuses_bound_instance_for_followup(tmp_path: Path) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        message_repo,
        execution_service,
    ) = _build_service(tmp_path / "task_orchestration_followup.db")
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    first_dispatch = await service.dispatch_task(run_id="run-1", task_id="task-1")
    first_task = cast(dict[str, JsonValue], first_dispatch["task"])
    bound_instance_id = str(first_task["instance_id"])
    second_dispatch = await service.dispatch_task(
        run_id=None,
        task_id="task-1",
        feedback="Add pagination to the response.",
    )

    assert second_dispatch["ok"] is True
    assert execution_service.calls == [
        (bound_instance_id, "spec_coder", created.envelope.task_id),
        (bound_instance_id, "spec_coder", created.envelope.task_id),
    ]
    history = message_repo.get_history_for_task(bound_instance_id, "task-1")
    assert len(history) == 1
    prompt_part = history[0].parts[0]
    assert isinstance(prompt_part, UserPromptPart)
    assert prompt_part.content == "Add pagination to the response."


@pytest.mark.asyncio
async def test_dispatch_task_returns_result_only_inside_task_projection(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
        _execution_service,
    ) = _build_service(tmp_path / "task_orchestration_dispatch_payload.db")
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    payload = await service.dispatch_task(run_id="run-1", task_id="task-1")

    task_payload = cast(dict[str, JsonValue], payload["task"])
    assert payload["ok"] is True
    assert "result" not in payload
    assert task_payload["result"] == "done:task-1"


@pytest.mark.asyncio
async def test_dispatch_task_reuses_session_role_instance_across_tasks(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
        _message_repo,
        execution_service,
    ) = _build_service(tmp_path / "task_orchestration_reuse_role_instance.db")
    first = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    second = task_repo.create(
        TaskEnvelope(
            task_id="task-2",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Refine endpoint",
            objective="Refine the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    first_dispatch = await service.dispatch_task(run_id="run-1", task_id="task-1")
    second_dispatch = await service.dispatch_task(run_id="run-1", task_id="task-2")

    first_task = cast(dict[str, JsonValue], first_dispatch["task"])
    second_task = cast(dict[str, JsonValue], second_dispatch["task"])
    assert first_task["instance_id"] == second_task["instance_id"]
    assert execution_service.calls == [
        (str(first_task["instance_id"]), "spec_coder", first.envelope.task_id),
        (str(second_task["instance_id"]), "spec_coder", second.envelope.task_id),
    ]
    session_agents = agent_repo.list_session_role_instances("session-1")
    assert len(session_agents) == 1
    assert session_agents[0].role_id == "spec_coder"


@pytest.mark.asyncio
async def test_dispatch_task_rejects_same_role_while_other_task_is_in_progress(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
        _execution_service,
    ) = _build_service(tmp_path / "task_orchestration_role_busy.db")
    first = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    second = task_repo.create(
        TaskEnvelope(
            task_id="task-2",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Refine endpoint",
            objective="Refine the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    first_dispatch = await service.dispatch_task(run_id="run-1", task_id="task-1")
    instance_id = str(cast(dict[str, JsonValue], first_dispatch["task"])["instance_id"])
    task_repo.update_status(
        first.envelope.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id=instance_id,
    )

    with pytest.raises(ValueError, match="role instance is busy"):
        await service.dispatch_task(run_id="run-1", task_id=second.envelope.task_id)

    second_record = task_repo.get(second.envelope.task_id)
    assert second_record.assigned_instance_id == instance_id
    assert second_record.status == TaskStatus.ASSIGNED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "feedback", "match"),
    [
        (TaskStatus.RUNNING, "", "already running"),
        (
            TaskStatus.COMPLETED,
            "",
            "feedback is required to re-dispatch a completed task",
        ),
        (TaskStatus.FAILED, "", "failed or timed out tasks must be recreated"),
        (TaskStatus.TIMEOUT, "", "failed or timed out tasks must be recreated"),
    ],
)
async def test_dispatch_task_rejects_invalid_statuses(
    tmp_path: Path,
    status: TaskStatus,
    feedback: str,
    match: str,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / f"task_orchestration_invalid_{status.value}.db"
    )
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_repo.update_status(
        created.envelope.task_id,
        status,
        assigned_instance_id="inst-existing",
    )

    with pytest.raises(ValueError, match=match):
        await service.dispatch_task(
            run_id="run-1",
            task_id=created.envelope.task_id,
            feedback=feedback,
        )
