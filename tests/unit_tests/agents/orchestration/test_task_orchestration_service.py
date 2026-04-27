# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pydantic import JsonValue

from pathlib import Path
from typing import cast

import pytest

import relay_teams.agents.orchestration.task_orchestration_service as task_orchestration_service_module
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookService
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
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskSpec,
    VerificationPlan,
)


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


class _BlockingTaskExecutionService:
    def __init__(self, task_repo: TaskRepository) -> None:
        self._task_repo = task_repo
        self.started_task_ids: list[str] = []
        self._started_events: dict[str, asyncio.Event] = {}
        self._release_events: dict[str, asyncio.Event] = {}

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> str:
        self.started_task_ids.append(task.task_id)
        self._started_event(task.task_id).set()
        await self._release_event(task.task_id).wait()
        result = f"done:{task.task_id}"
        self._task_repo.update_status(
            task.task_id,
            TaskStatus.COMPLETED,
            assigned_instance_id=instance_id,
            result=result,
        )
        return result

    async def wait_started(self, task_id: str) -> None:
        await asyncio.wait_for(self._started_event(task_id).wait(), timeout=1.0)

    def release(self, task_id: str) -> None:
        self._release_event(task_id).set()

    def _started_event(self, task_id: str) -> asyncio.Event:
        event = self._started_events.get(task_id)
        if event is None:
            event = asyncio.Event()
            self._started_events[task_id] = event
        return event

    def _release_event(self, task_id: str) -> asyncio.Event:
        event = self._release_events.get(task_id)
        if event is None:
            event = asyncio.Event()
            self._release_events[task_id] = event
        return event


class _CapturingHookService:
    def __init__(self, decision: HookDecisionType = HookDecisionType.ALLOW) -> None:
        self.decision = decision
        self.calls: list[tuple[object, object | None]] = []

    async def execute(
        self,
        *,
        event_input: object,
        run_event_hub: object | None,
    ) -> HookDecisionBundle:
        self.calls.append((event_input, run_event_hub))
        return HookDecisionBundle(decision=self.decision)


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


def _create_assigned_task(
    *,
    task_repo: TaskRepository,
    task_id: str,
    session_id: str,
    run_id: str,
    instance_id: str,
) -> None:
    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=f"{run_id}-root",
            trace_id=run_id,
            role_id="spec_coder",
            title=f"Task {task_id}",
            objective=f"Execute {task_id}",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_repo.update_status(
        task_id=task_id,
        status=TaskStatus.ASSIGNED,
        assigned_instance_id=instance_id,
    )


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
async def test_create_tasks_persists_spec_verification_and_lifecycle(
    tmp_path: Path,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_spec.db"
    )

    payload = await service.create_tasks(
        run_id="run-1",
        tasks=[
            TaskDraft(
                objective="Implement the endpoint",
                spec=TaskSpec(
                    summary="Endpoint contract",
                    acceptance_criteria=("returns 201",),
                    evidence_expectations=("pytest",),
                    verification_commands=("pytest tests/unit_tests/agents/tasks",),
                ),
                lifecycle=TaskLifecyclePolicy(
                    timeout_seconds=60,
                    heartbeat_interval_seconds=5,
                ),
            )
        ],
    )

    created_task = cast(
        dict[str, JsonValue], cast(list[JsonValue], payload["tasks"])[0]
    )
    task_id = str(created_task["task_id"])
    record = task_repo.get(task_id)
    spec_payload = cast(dict[str, JsonValue], created_task["spec"])
    verification_payload = cast(dict[str, JsonValue], created_task["verification"])
    lifecycle_payload = cast(dict[str, JsonValue], created_task["lifecycle"])

    assert record.envelope.spec is not None
    assert record.envelope.spec.acceptance_criteria == ("returns 201",)
    assert record.envelope.verification.acceptance_criteria == ("returns 201",)
    assert record.envelope.verification.command_checks[0].command == (
        "pytest",
        "tests/unit_tests/agents/tasks",
    )
    assert spec_payload["summary"] == "Endpoint contract"
    assert verification_payload["acceptance_criteria"] == ["returns 201"]
    assert verification_payload["command_checks"] == [
        {
            "command": ["pytest", "tests/unit_tests/agents/tasks"],
            "cwd": None,
            "timeout_seconds": 120.0,
        }
    ]
    assert verification_payload["evidence_expectations"] == ["pytest"]
    assert lifecycle_payload["timeout_seconds"] == 60


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


@pytest.mark.asyncio
async def test_create_tasks_denied_by_hook_does_not_persist_task(
    tmp_path: Path,
) -> None:
    task_repo = TaskRepository(tmp_path / "task_orchestration_hook_deny.db")
    agent_repo = AgentInstanceRepository(tmp_path / "task_orchestration_hook_deny.db")
    message_repo = MessageRepository(tmp_path / "task_orchestration_hook_deny.db")
    session_repo = SessionRepository(tmp_path / "task_orchestration_hook_deny.db")
    execution_service = _FakeTaskExecutionService(task_repo)
    hook_service = _CapturingHookService(HookDecisionType.DENY)
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
        run_event_hub=cast(RunEventHub, object()),
    )

    with pytest.raises(ValueError, match="Task creation denied"):
        await service.create_tasks(
            run_id="run-1",
            tasks=[TaskDraft(objective="Implement the endpoint")],
        )

    assert [record.envelope.task_id for record in task_repo.list_all()] == ["task-root"]


@pytest.mark.asyncio
async def test_update_task_allows_created_only(tmp_path: Path) -> None:
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

    updated = await service.update_task_async(
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
        await service.update_task_async(
            run_id="run-1",
            task_id=created.envelope.task_id,
            update=TaskUpdate(title="Should fail"),
        )


@pytest.mark.asyncio
async def test_update_task_recomputes_verification_when_spec_changes(
    tmp_path: Path,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_update_spec.db"
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
            verification=VerificationPlan(
                checklist=("non_empty_response",),
                acceptance_criteria=("old acceptance",),
            ),
            spec=TaskSpec(acceptance_criteria=("old acceptance",)),
        )
    )

    updated = await service.update_task_async(
        run_id="run-1",
        task_id=created.envelope.task_id,
        update=TaskUpdate(
            spec=TaskSpec(
                summary="Updated contract",
                acceptance_criteria=("new acceptance",),
                evidence_expectations=("pytest output",),
                verification_commands=("pytest tests/unit_tests/agents/tasks",),
            ),
        ),
    )
    updated_record = task_repo.get(created.envelope.task_id)
    updated_task = cast(dict[str, JsonValue], updated["task"])
    verification_payload = cast(dict[str, JsonValue], updated_task["verification"])

    assert updated_record.envelope.spec is not None
    assert updated_record.envelope.spec.summary == "Updated contract"
    assert updated_record.envelope.verification.acceptance_criteria == (
        "new acceptance",
    )
    assert updated_record.envelope.verification.command_checks[0].command == (
        "pytest",
        "tests/unit_tests/agents/tasks",
    )
    assert verification_payload["acceptance_criteria"] == ["new acceptance"]
    assert verification_payload["evidence_expectations"] == ["pytest output"]


@pytest.mark.asyncio
async def test_update_task_handoff_only_preserves_missing_title(
    tmp_path: Path,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_handoff_only.db"
    )
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="spec_coder",
            title=None,
            objective="Initial objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_repo.update_status(created.envelope.task_id, TaskStatus.ASSIGNED)

    updated = await service.update_task_async(
        run_id="run-1",
        task_id=created.envelope.task_id,
        update=TaskUpdate(
            handoff=TaskHandoff(
                incomplete=("Collect more logs",),
                reason="waiting on operator",
            )
        ),
    )

    updated_record = task_repo.get(created.envelope.task_id)
    updated_task = cast(dict[str, JsonValue], updated["task"])

    assert updated_record.envelope.title is None
    assert updated_task["title"] == "Initial objective"
    assert cast(dict[str, JsonValue], updated_task["handoff"])["reason"] == (
        "waiting on operator"
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
async def test_dispatch_task_limits_execution_slots_per_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_orchestration_service_module,
        "MAX_PARALLEL_DELEGATED_TASKS",
        1,
    )
    db_path = tmp_path / "task_orchestration_per_run_slots.db"
    task_repo = TaskRepository(db_path)
    execution_service = _BlockingTaskExecutionService(task_repo)
    service = TaskOrchestrationService(
        task_repo=task_repo,
        role_registry=_build_role_registry(),
        agent_repo=AgentInstanceRepository(db_path),
        task_execution_service=cast(TaskExecutionService, execution_service),
        message_repo=MessageRepository(db_path),
    )
    _create_assigned_task(
        task_repo=task_repo,
        task_id="task-run-1",
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-run-1",
    )
    _create_assigned_task(
        task_repo=task_repo,
        task_id="task-run-2",
        session_id="session-2",
        run_id="run-2",
        instance_id="inst-run-2",
    )

    first_dispatch = asyncio.create_task(
        service.dispatch_task(
            run_id="run-1",
            task_id="task-run-1",
            role_id="spec_coder",
        )
    )
    await execution_service.wait_started("task-run-1")
    second_dispatch = asyncio.create_task(
        service.dispatch_task(
            run_id="run-2",
            task_id="task-run-2",
            role_id="spec_coder",
        )
    )
    try:
        await execution_service.wait_started("task-run-2")
    finally:
        execution_service.release("task-run-1")
        execution_service.release("task-run-2")
        dispatch_results = await asyncio.gather(
            first_dispatch,
            second_dispatch,
            return_exceptions=True,
        )
        for dispatch_result in dispatch_results:
            if isinstance(dispatch_result, BaseException):
                raise dispatch_result

    assert execution_service.started_task_ids == ["task-run-1", "task-run-2"]
    assert service._execution_semaphores == {}
    assert service._execution_semaphore_ref_counts == {}


@pytest.mark.asyncio
async def test_dispatch_task_binds_unassigned_created_task_to_requested_role(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
        execution_service,
    ) = _build_service(tmp_path / "task_orchestration_bind_unassigned.db")
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id=None,
            title="Implement endpoint",
            objective="Implement the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    payload = await service.dispatch_task(
        run_id="run-1",
        task_id=created.envelope.task_id,
        role_id="spec_coder",
    )

    record = task_repo.get(created.envelope.task_id)
    task_payload = cast(dict[str, JsonValue], payload["task"])
    assert record.envelope.role_id == "spec_coder"
    assert task_payload["assigned_role_id"] == "spec_coder"
    assert execution_service.calls[0][1:3] == ("spec_coder", created.envelope.task_id)
    assert service._assignment_locks == {}
    assert service._assignment_lock_ref_counts == {}


@pytest.mark.asyncio
async def test_dispatch_task_rejects_created_task_role_rebinding(
    tmp_path: Path,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_rebind_created.db"
    )
    created = task_repo.create(
        TaskEnvelope(
            task_id="task-1",
            session_id="session-1",
            parent_task_id="task-root",
            trace_id="run-1",
            role_id="reviewer",
            title="Review endpoint",
            objective="Review the endpoint",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    with pytest.raises(ValueError, match="already bound to role reviewer"):
        await service.dispatch_task(
            run_id="run-1",
            task_id=created.envelope.task_id,
            role_id="spec_coder",
        )


@pytest.mark.asyncio
async def test_dispatch_task_uses_assignment_done_by_parallel_dispatch(
    tmp_path: Path,
) -> None:
    (
        service,
        task_repo,
        _agent_repo,
        _message_repo,
        execution_service,
    ) = _build_service(tmp_path / "task_orchestration_parallel_assignment.db")
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
    async with service._role_assignment_lock_slot(
        session_id="session-1",
        role_id="spec_coder",
    ) as assignment_lock:
        await assignment_lock.acquire()
        dispatch_task = asyncio.create_task(
            service.dispatch_task(
                run_id="run-1",
                task_id=created.envelope.task_id,
                role_id="spec_coder",
            )
        )
        await asyncio.sleep(0)
        task_repo.update_status(
            created.envelope.task_id,
            TaskStatus.ASSIGNED,
            assigned_instance_id="inst-existing",
        )
        assignment_lock.release()

    payload = await dispatch_task

    task_payload = cast(dict[str, JsonValue], payload["task"])
    assert task_payload["assigned_instance_id"] == "inst-existing"
    assert execution_service.calls == [
        (
            "inst-existing",
            "spec_coder",
            created.envelope.task_id,
            "Execute this task contract and return the requested result.",
        )
    ]
    assert service._assignment_locks == {}
    assert service._assignment_lock_ref_counts == {}


@pytest.mark.asyncio
async def test_dispatch_task_revalidates_role_after_parallel_assignment(
    tmp_path: Path,
) -> None:
    service, task_repo, _agent_repo, _message_repo, _execution_service = _build_service(
        tmp_path / "task_orchestration_parallel_assignment_role.db"
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
    async with service._role_assignment_lock_slot(
        session_id="session-1",
        role_id="spec_coder",
    ) as assignment_lock:
        await assignment_lock.acquire()
        dispatch_task = asyncio.create_task(
            service.dispatch_task(
                run_id="run-1",
                task_id=created.envelope.task_id,
                role_id="spec_coder",
            )
        )
        await asyncio.sleep(0)
        _ = task_repo.update_envelope(
            created.envelope.task_id,
            created.envelope.model_copy(update={"role_id": "reviewer"}),
        )
        task_repo.update_status(
            created.envelope.task_id,
            TaskStatus.ASSIGNED,
            assigned_instance_id="inst-reviewer",
        )
        assignment_lock.release()

    with pytest.raises(ValueError, match="already bound to role reviewer"):
        await dispatch_task
    assert service._assignment_locks == {}
    assert service._assignment_lock_ref_counts == {}


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


@pytest.mark.asyncio
async def test_list_run_tasks_omits_inner_ok(tmp_path: Path) -> None:
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

    payload = await service.list_delegated_tasks_async(run_id="run-1")

    assert "ok" not in payload
    tasks = cast(list[JsonValue], payload["tasks"])
    assert len(tasks) == 1
