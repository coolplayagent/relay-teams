# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from pathlib import Path
from typing import cast

import pytest

from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.hooks import HookService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry

from relay_teams.agents.instances.enums import InstanceLifecycle
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionService
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _FakeTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.calls: list[tuple[str, str, str, str | None]] = []

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
        self.calls.append((instance_id, role_id, task.task_id, user_prompt_override))
        result = f"done:{task.task_id}"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return result


class _CapturingHookService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object | None]] = []

    async def execute(
        self,
        *,
        event_input: object,
        run_event_hub: object | None,
    ) -> object:
        self.calls.append((event_input, run_event_hub))
        return object()


def _build_role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
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
            role_id="Coordinator",
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
async def test_create_tasks_creates_unassigned_task_contracts(tmp_path: Path) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
        _execution_service,
    ) = _build_service(tmp_path / "task_orchestration_create.db")

    payload = await service.create_tasks(
        run_id="run-1",
        tasks=[
            TaskDraft(
                objective="Implement the endpoint",
                title="Endpoint implementation",
            )
        ],
    )

    tasks_payload = cast(list[JsonValue], payload["tasks"])
    created_task = cast(dict[str, JsonValue], tasks_payload[0])
    task_id = str(created_task["task_id"])
    record = task_repo.get(task_id)

    assert payload["created_count"] == 1
    assert record.envelope.parent_task_id == "task-root"
    assert record.envelope.role_id is None
    assert record.envelope.title == "Endpoint implementation"
    assert record.status == TaskStatus.CREATED
    assert record.assigned_instance_id is None
    assert created_task["assigned_role_id"] is None
    assert created_task["assigned_instance_id"] is None


@pytest.mark.asyncio
async def test_create_tasks_emits_task_created_hooks_with_created_task_identity(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "task_orchestration_hooks.db")
    agent_repo = AgentInstanceRepository(tmp_path / "task_orchestration_hooks.db")
    message_repo = MessageRepository(tmp_path / "task_orchestration_hooks.db")
    session_repo = SessionRepository(tmp_path / "task_orchestration_hooks.db")
    execution_service = _FakeTaskExecutionService(task_repo)
    hook_service = _CapturingHookService()
    run_event_hub = cast(RunEventHub, object())
    _seed_root_task(task_repo)
    _ = session_repo.create(session_id="session-1", workspace_id="default")
    service = TaskOrchestrationService(
        task_repo=task_repo,
        role_registry=_build_role_registry(),
        agent_repo=agent_repo,
        task_execution_service=cast(TaskExecutionService, execution_service),
        message_repo=message_repo,
        session_repo=session_repo,
        hook_service=cast(HookService, hook_service),
        run_event_hub=run_event_hub,
    )

    payload = await service.create_tasks(
        run_id="run-1",
        tasks=[TaskDraft(objective="Implement the endpoint")],
    )

    created_task = cast(
        dict[str, JsonValue], cast(list[JsonValue], payload["tasks"])[0]
    )
    created_task_id = str(created_task["task_id"])
    assert len(hook_service.calls) == 1
    event_input, captured_run_event_hub = hook_service.calls[0]
    assert getattr(event_input, "created_task_id") == created_task_id
    assert getattr(event_input, "task_id") == created_task_id
    assert getattr(event_input, "parent_task_id") == "task-root"
    assert captured_run_event_hub is run_event_hub


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
            objective="Review the implementation",
            title="Code review",
        ),
    )
    updated_record = task_repo.get(created.envelope.task_id)
    updated_task = cast(dict[str, JsonValue], updated["task"])

    assert updated_record.envelope.role_id == "spec_coder"
    assert updated_record.envelope.objective == "Review the implementation"
    assert updated_record.envelope.title == "Code review"
    assert updated_task["assigned_role_id"] == "spec_coder"
    assert updated_task["title"] == "Code review"

    task_repo.update_status(created.envelope.task_id, TaskStatus.ASSIGNED)
    with pytest.raises(ValueError, match="only created tasks can be updated"):
        service.update_task(
            run_id="run-1",
            task_id=created.envelope.task_id,
            update=TaskUpdate(title="Should fail"),
        )


@pytest.mark.asyncio
async def test_dispatch_task_rejects_followup_prompt_for_completed_task(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
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

    first_dispatch = await service.dispatch_task(
        run_id="run-1",
        task_id="task-1",
        role_id="spec_coder",
    )
    first_task = cast(dict[str, JsonValue], first_dispatch["task"])
    bound_instance_id = str(first_task["assigned_instance_id"])
    with pytest.raises(
        ValueError,
        match="Create a replacement task instead of re-dispatching this one",
    ):
        await service.dispatch_task(
            run_id=None,
            task_id="task-1",
            role_id="spec_coder",
            prompt="Add pagination to the response.",
        )

    assert execution_service.calls == [
        (
            bound_instance_id,
            "spec_coder",
            created.envelope.task_id,
            "Execute this task contract and return the requested result.",
        ),
    ]


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

    payload = await service.dispatch_task(
        run_id="run-1",
        task_id="task-1",
        role_id="spec_coder",
    )

    task_payload = cast(dict[str, JsonValue], payload["task"])
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

    first_dispatch = await service.dispatch_task(
        run_id="run-1",
        task_id="task-1",
        role_id="spec_coder",
    )
    second_dispatch = await service.dispatch_task(
        run_id="run-1",
        task_id="task-2",
        role_id="spec_coder",
    )

    first_task = cast(dict[str, JsonValue], first_dispatch["task"])
    second_task = cast(dict[str, JsonValue], second_dispatch["task"])
    assert first_task["assigned_instance_id"] == second_task["assigned_instance_id"]
    assert len(execution_service.calls) == 2
    assert execution_service.calls[0][:3] == (
        str(first_task["assigned_instance_id"]),
        "spec_coder",
        first.envelope.task_id,
    )
    assert execution_service.calls[1][:3] == (
        str(second_task["assigned_instance_id"]),
        "spec_coder",
        second.envelope.task_id,
    )
    session_agents = agent_repo.list_session_role_instances("session-1")
    assert len(session_agents) == 1
    assert session_agents[0].role_id == "spec_coder"


@pytest.mark.asyncio
async def test_dispatch_task_clones_same_role_while_reusable_instance_is_busy(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        agent_repo,
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

    first_dispatch = await service.dispatch_task(
        run_id="run-1",
        task_id="task-1",
        role_id="spec_coder",
    )
    instance_id = str(
        cast(dict[str, JsonValue], first_dispatch["task"])["assigned_instance_id"]
    )
    task_repo.update_status(
        first.envelope.task_id,
        TaskStatus.RUNNING,
        assigned_instance_id=instance_id,
    )

    second_dispatch = await service.dispatch_task(
        run_id="run-1",
        task_id=second.envelope.task_id,
        role_id="spec_coder",
    )

    second_record = task_repo.get(second.envelope.task_id)
    second_task = cast(dict[str, JsonValue], second_dispatch["task"])
    clone_instance_id = str(second_task["assigned_instance_id"])
    assert clone_instance_id != instance_id
    assert second_record.assigned_instance_id == clone_instance_id
    assert second_record.status == TaskStatus.COMPLETED

    clone = agent_repo.get_instance(clone_instance_id)
    assert clone.lifecycle == InstanceLifecycle.EPHEMERAL
    assert clone.parent_instance_id == instance_id
    session_agents = agent_repo.list_session_role_instances("session-1")
    assert len(session_agents) == 1
    assert session_agents[0].instance_id == instance_id
    assert session_agents[0].lifecycle == InstanceLifecycle.REUSABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "prompt", "match"),
    [
        (TaskStatus.RUNNING, "", "already running"),
        (
            TaskStatus.COMPLETED,
            "Add pagination to the response.",
            "Create a replacement task instead of re-dispatching this one",
        ),
        (
            TaskStatus.FAILED,
            "",
            "Create a replacement task instead of re-dispatching this one",
        ),
        (
            TaskStatus.TIMEOUT,
            "",
            "Create a replacement task instead of re-dispatching this one",
        ),
    ],
)
async def test_dispatch_task_rejects_invalid_statuses(
    tmp_path: Path,
    status: TaskStatus,
    prompt: str,
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
            role_id="spec_coder",
            prompt=prompt,
        )


def test_list_run_tasks_omits_inner_ok(tmp_path: Path) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_list.db"
    )
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

    payload = service.list_delegated_tasks(run_id="run-1")

    assert "ok" not in payload
    tasks = cast(list[JsonValue], payload["tasks"])
    assert len(tasks) == 1
