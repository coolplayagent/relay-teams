from __future__ import annotations

from typing import cast

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.agents.instances.models import AgentRuntimeRecord
from agent_teams.sessions.rounds_projection import build_session_rounds
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.workspace import build_conversation_id


class _FakeAgentRepo:
    def __init__(self, agents: tuple[AgentRuntimeRecord, ...] = ()) -> None:
        self._agents = agents

    def list_by_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        return self._agents

    def list_session_role_instances(
        self, session_id: str
    ) -> tuple[AgentRuntimeRecord, ...]:
        return self._agents


class _FakeTaskRepo:
    def __init__(self, tasks: tuple[TaskRecord, ...] = ()) -> None:
        self._tasks = tasks

    def list_by_session(self, session_id: str) -> tuple[TaskRecord, ...]:
        return self._tasks


class _FakeRunRuntimeRepo:
    def __init__(self, runtimes: tuple[RunRuntimeRecord, ...] = ()) -> None:
        self._runtimes = runtimes

    def list_by_session(self, session_id: str) -> tuple[RunRuntimeRecord, ...]:
        return self._runtimes


def test_build_session_rounds_uses_task_bound_role_instance_map() -> None:
    session_id = "session-1"
    run_id = "run-1"
    role_id = "spec_coder"

    agent = AgentRuntimeRecord(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-new",
        role_id=role_id,
        workspace_id="default",
        conversation_id=build_conversation_id(session_id, role_id),
        status=InstanceStatus.IDLE,
    )
    runtime = RunRuntimeRecord(
        run_id=run_id,
        session_id=session_id,
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    delegated_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-1",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            role_id=role_id,
            objective="implement",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-new",
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(
            AgentInstanceRepository,
            cast(object, _FakeAgentRepo((agent,))),
        ),
        task_repo=cast(
            TaskRepository,
            cast(object, _FakeTaskRepo((root_task, delegated_task))),
        ),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo((runtime,))),
        ),
        get_session_messages=lambda _: [],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    instance_role_map = cast(dict[str, str], round_item["instance_role_map"])
    role_instance_map = cast(dict[str, str], round_item["role_instance_map"])
    assert instance_role_map == {"inst-new": role_id}
    assert role_instance_map[role_id] == "inst-new"


def test_build_session_rounds_includes_task_instance_map() -> None:
    session_id = "session-1"
    run_id = "run-1"
    root_task = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="root",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id=None,
    )
    task_first = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-first",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="first",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-first",
    )
    task_second = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-second",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="second",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id="inst-second",
    )
    task_without_instance = TaskRecord(
        envelope=TaskEnvelope(
            task_id="task-unassigned",
            session_id=session_id,
            parent_task_id="task-root",
            trace_id=run_id,
            objective="unassigned",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        ),
        assigned_instance_id=None,
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(
            TaskRepository,
            cast(
                object,
                _FakeTaskRepo(
                    (root_task, task_first, task_second, task_without_instance)
                ),
            ),
        ),
        approval_tickets_by_run={},
        run_runtime_repo=cast(
            RunRuntimeRepository,
            cast(object, _FakeRunRuntimeRepo()),
        ),
        get_session_messages=lambda _: [],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    task_instance_map = cast(dict[str, str], round_item["task_instance_map"])
    task_status_map = cast(dict[str, str], round_item["task_status_map"])
    assert task_instance_map == {
        "task-first": "inst-first",
        "task-second": "inst-second",
    }
    assert task_status_map == {
        "task-root": "created",
        "task-first": "created",
        "task-second": "created",
        "task-unassigned": "created",
    }
