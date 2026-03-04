from __future__ import annotations

import json
from typing import cast

from agent_teams.application.rounds_projection import build_session_rounds
from agent_teams.core.enums import RunEventType


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
        event_log=_FakeEventLog(events),  # type: ignore
        agent_repo=_FakeAgentRepo(),  # type: ignore
        task_repo=_FakeTaskRepo(),  # type: ignore
        shared_store=_FakeSharedStore(),  # type: ignore
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
