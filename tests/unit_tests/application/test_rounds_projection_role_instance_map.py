from __future__ import annotations

import json
from typing import cast

from agent_teams.application.rounds_projection import build_session_rounds
from agent_teams.core.models import TaskEnvelope, TaskRecord, VerificationPlan
from agent_teams.core.enums import RunEventType
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.shared_store import SharedStore
from agent_teams.state.task_repo import TaskRepository


class _FakeEventLog:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = tuple(events)

    def list_by_session(self, session_id: str) -> tuple[dict[str, object], ...]:
        return self._events


class _FakeAgentRepo:
    def list_by_session(self, session_id: str) -> tuple[object, ...]:
        return ()


class _FakeTaskRepo:
    def list_by_session(self, session_id: str) -> tuple[object, ...]:
        return ()


class _FakeTaskRepoWithTasks:
    def __init__(self, tasks: tuple[TaskRecord, ...]) -> None:
        self._tasks = tasks

    def list_by_session(self, session_id: str) -> tuple[TaskRecord, ...]:
        return self._tasks


class _FakeSharedStore:
    def get_state(self, scope, key: str):  # pragma: no cover - shape-only fake
        return None


def test_build_session_rounds_uses_latest_instance_for_same_role() -> None:
    session_id = "session-1"
    run_id = "run-1"
    role_id = "spec_coder"

    events: list[dict[str, object]] = [
        {
            "event_type": RunEventType.MODEL_STEP_STARTED.value,
            "trace_id": run_id,
            "session_id": session_id,
            "task_id": "task-1",
            "instance_id": "inst-old",
            "payload_json": json.dumps(
                {
                    "role_id": role_id,
                    "instance_id": "inst-old",
                }
            ),
            "occurred_at": "2026-03-04T01:00:00+00:00",
        },
        {
            "event_type": RunEventType.MODEL_STEP_STARTED.value,
            "trace_id": run_id,
            "session_id": session_id,
            "task_id": "task-1",
            "instance_id": "inst-new",
            "payload_json": json.dumps(
                {
                    "role_id": role_id,
                    "instance_id": "inst-new",
                }
            ),
            "occurred_at": "2026-03-04T01:00:01+00:00",
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        event_log=cast(EventLog, cast(object, _FakeEventLog(events))),
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _FakeTaskRepo())),
        shared_store=cast(SharedStore, cast(object, _FakeSharedStore())),
        get_session_messages=lambda _: [],
    )

    assert len(rounds) == 1
    round_item = rounds[0]
    instance_role_map = cast(dict[str, str], round_item["instance_role_map"])
    role_instance_map = cast(dict[str, str], round_item["role_instance_map"])
    assert instance_role_map == {
        "inst-old": role_id,
        "inst-new": role_id,
    }
    assert role_instance_map[role_id] == "inst-new"


def test_build_session_rounds_includes_task_instance_map() -> None:
    session_id = "session-1"
    run_id = "run-1"
    events: list[dict[str, object]] = [
        {
            "event_type": RunEventType.RUN_STARTED.value,
            "trace_id": run_id,
            "session_id": session_id,
            "task_id": None,
            "instance_id": None,
            "payload_json": "{}",
            "occurred_at": "2026-03-04T01:00:00+00:00",
        }
    ]
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
        event_log=cast(EventLog, cast(object, _FakeEventLog(events))),
        agent_repo=cast(AgentInstanceRepository, cast(object, _FakeAgentRepo())),
        task_repo=cast(
            TaskRepository,
            cast(
                object,
                _FakeTaskRepoWithTasks((task_first, task_second, task_without_instance)),
            ),
        ),
        shared_store=cast(SharedStore, cast(object, _FakeSharedStore())),
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
        "task-first": "created",
        "task-second": "created",
        "task-unassigned": "created",
    }
