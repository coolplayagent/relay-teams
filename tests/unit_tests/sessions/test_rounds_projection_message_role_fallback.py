from __future__ import annotations

import json
from pathlib import Path

import pytest
from typing import cast

from pydantic_ai.messages import (
    ImageUrl,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agent_runtimes.instances.enums import InstanceStatus
from relay_teams.sessions.session_rounds_projection import build_session_rounds
from relay_teams.sessions.session_rounds_projection import build_session_timeline_rounds
from relay_teams.sessions.session_rounds_projection import (
    _coordinator_event_tool_messages,
    _has_assistant_text_message,
    _merge_event_tool_messages,
)
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunResult
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.workspace import build_conversation_id


def _tool_message(
    *,
    role: str,
    part_kind: str,
    tool_call_id: str,
    created_at: str,
    content: str,
) -> dict[str, object]:
    return {
        "role": role,
        "created_at": created_at,
        "message": {
            "parts": [
                {
                    "part_kind": part_kind,
                    "tool_name": "shell",
                    "tool_call_id": tool_call_id,
                    "content": content,
                }
            ]
        },
    }


def _assistant_history_message(
    *,
    run_id: str,
    task_id: str,
    created_at: str,
    parts: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "conversation_id": "conv-session-1-Coordinator",
        "agent_role_id": "Coordinator",
        "instance_id": "inst-coordinator",
        "task_id": task_id,
        "trace_id": run_id,
        "role": "assistant",
        "role_id": "Coordinator",
        "created_at": created_at,
        "message": {"parts": parts},
    }


def test_coordinator_event_tool_messages_supports_instance_fallback() -> None:
    coordinator_message: dict[str, object] = {
        "role_id": "",
        "agent_role_id": "",
        "instance_id": "inst-coordinator",
    }
    worker_message: dict[str, object] = {
        "role_id": "Worker",
        "agent_role_id": "Worker",
        "instance_id": "inst-worker",
    }

    assert _coordinator_event_tool_messages(
        [coordinator_message, worker_message],
        coordinator_role_id=None,
        coordinator_instance_id="inst-coordinator",
    ) == [coordinator_message]
    assert (
        _coordinator_event_tool_messages(
            [coordinator_message],
            coordinator_role_id=None,
            coordinator_instance_id=None,
        )
        == []
    )


def test_has_assistant_text_message_handles_non_matching_messages() -> None:
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "message": {
                "parts": [
                    {
                        "part_kind": "text",
                        "content": "terminal final answer",
                    }
                ]
            },
        },
        {
            "role": "assistant",
            "message": {"parts": [{"part_kind": "tool-call", "tool_name": "shell"}]},
        },
    ]

    assert _has_assistant_text_message(messages, "") is False
    assert _has_assistant_text_message(messages, "terminal final answer") is False


def test_has_assistant_text_message_matches_combined_text_parts() -> None:
    messages: list[dict[str, object]] = [
        {
            "role": "assistant",
            "message": {
                "parts": [
                    {"part_kind": "text", "content": "first paragraph"},
                    {"part_kind": "text", "content": "second paragraph"},
                ]
            },
        }
    ]

    assert (
        _has_assistant_text_message(messages, "first paragraph\n\nsecond paragraph")
        is True
    )


def test_coordinator_event_tool_messages_prefers_instance_over_shared_role() -> None:
    coordinator_message: dict[str, object] = {
        "role_id": "Main Agent",
        "agent_role_id": "Main Agent",
        "instance_id": "inst-coordinator",
    }
    same_role_worker_message: dict[str, object] = {
        "role_id": "Main Agent",
        "agent_role_id": "Main Agent",
        "instance_id": "inst-worker",
    }

    messages = [coordinator_message, same_role_worker_message]

    assert _coordinator_event_tool_messages(
        messages,
        coordinator_role_id="Main Agent",
        coordinator_instance_id="inst-coordinator",
    ) == [coordinator_message]
    assert (
        _coordinator_event_tool_messages(
            messages,
            coordinator_role_id="Main Agent",
            coordinator_instance_id=None,
        )
        == messages
    )


def test_merge_event_tool_messages_preserves_repeated_tool_call_occurrences() -> None:
    existing_call = _tool_message(
        role="assistant",
        part_kind="tool-call",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:00Z",
        content="first call",
    )
    existing_result = _tool_message(
        role="user",
        part_kind="tool-return",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:01Z",
        content="first result",
    )
    first_event_call = _tool_message(
        role="assistant",
        part_kind="tool-call",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:00Z",
        content="first event call",
    )
    first_event_result = _tool_message(
        role="user",
        part_kind="tool-return",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:01Z",
        content="first event result",
    )
    second_event_call = _tool_message(
        role="assistant",
        part_kind="tool-call",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:02Z",
        content="second event call",
    )
    second_event_result = _tool_message(
        role="user",
        part_kind="tool-return",
        tool_call_id="call_1",
        created_at="2026-04-29T10:00:03Z",
        content="second event result",
    )

    merged = _merge_event_tool_messages(
        [existing_call, existing_result],
        [
            first_event_call,
            first_event_result,
            second_event_call,
            second_event_result,
        ],
    )

    assert merged == [
        existing_call,
        existing_result,
        second_event_call,
        second_event_result,
    ]


def test_build_session_rounds_maps_role_by_instance_across_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "rounds_projection_role_fallback.db"
    session_id = "session-1"
    old_run_id = "run-old"
    new_run_id = "run-new"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-old",
            session_id=session_id,
            parent_task_id=None,
            trace_id=old_run_id,
            objective="old objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-new",
            session_id=session_id,
            parent_task_id=None,
            trace_id=new_run_id,
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=old_run_id,
        trace_id=old_run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=new_run_id,
        session_id=session_id,
        root_task_id="task-root-new",
    )

    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id=coordinator_instance_id,
        task_id="task-root-new",
        trace_id=new_run_id,
        messages=[
            ModelRequest(parts=[UserPromptPart(content="what color is a rainbow")]),
            ModelResponse(
                parts=[TextPart(content="Rainbows usually have seven colors.")]
            ),
        ],
    )

    def _session_messages(sid: str) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], message_repo.get_messages_by_session(sid))

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=_session_messages,
    )
    round_new = next(item for item in rounds if item["run_id"] == new_run_id)

    assert round_new["has_user_messages"] is True
    coordinator_messages = cast(
        list[dict[str, object]], round_new["coordinator_messages"]
    )
    assert len(coordinator_messages) == 1
    assert coordinator_messages[0].get("role_id") == "Coordinator"


def test_build_session_rounds_projects_public_injection_messages(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_injection.db"
    session_id = "session-1"
    run_id = "run-injection"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    events: list[dict[str, object]] = [
        {
            "trace_id": run_id,
            "run_id": run_id,
            "event_type": RunEventType.INJECTION_ENQUEUED.value,
            "occurred_at": "2026-04-29T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "injection_id": "inj_focus",
                    "run_id": run_id,
                    "recipient_instance_id": "inst-main",
                    "source": "user",
                    "delivery_mode": "interrupt",
                    "visibility": "public",
                    "content": "focus on tests",
                    "priority": 1,
                    "created_at": "2026-04-29T10:00:00+00:00",
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "event_type": RunEventType.INJECTION_APPLIED.value,
            "occurred_at": "2026-04-29T10:00:02+00:00",
            "payload_json": json.dumps(
                {
                    "injection_id": "inj_focus",
                    "run_id": run_id,
                    "recipient_instance_id": "inst-main",
                    "source": "user",
                    "delivery_mode": "interrupt",
                    "visibility": "public",
                    "content": "focus on tests",
                    "priority": 1,
                    "created_at": "2026-04-29T10:00:00+00:00",
                    "interrupted_current_step": True,
                    "restart_scope": "interrupt",
                    "supersedes_pending_tool_calls": False,
                }
            ),
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )
    timeline = build_session_timeline_rounds(
        session_id=session_id,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_user_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )

    injection_messages = cast(list[dict[str, object]], rounds[0]["injection_messages"])
    assert len(injection_messages) == 1
    assert injection_messages[0]["status"] == "applied"
    assert injection_messages[0]["injection_id"] == "inj_focus"
    assert injection_messages[0]["applied_at"] == "2026-04-29T10:00:02+00:00"
    assert injection_messages[0]["occurred_at"] == "2026-04-29T10:00:02+00:00"
    assert injection_messages[0]["mode"] == "interrupt"
    assert injection_messages[0]["content"] == "focus on tests"
    assert injection_messages[0]["interrupted_current_step"] is True
    assert injection_messages[0]["restart_scope"] == "interrupt"
    assert injection_messages[0]["supersedes_pending_tool_calls"] is False
    assert timeline[0]["injection_messages"] == injection_messages


def test_build_session_rounds_omits_internal_injection_payloads(tmp_path: Path) -> None:
    db_path = tmp_path / "rounds_projection_internal_injection.db"
    run_id = "run-injection"
    events: list[dict[str, object]] = [
        {
            "trace_id": run_id,
            "event_type": RunEventType.INJECTION_ENQUEUED.value,
            "occurred_at": "2026-04-29T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "recipient_instance_id": "inst-main",
                    "source": "system",
                    "delivery_mode": "queued",
                    "visibility": "internal",
                    "content_redacted": True,
                    "content_length": 12,
                    "created_at": "2026-04-29T10:00:00+00:00",
                }
            ),
        }
    ]

    rounds = build_session_rounds(
        session_id="session-1",
        agent_repo=AgentInstanceRepository(db_path),
        task_repo=TaskRepository(db_path),
        approval_tickets_by_run={},
        run_runtime_repo=RunRuntimeRepository(db_path),
        get_session_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )

    assert rounds == []


def test_build_session_rounds_keeps_tool_outcome_messages_for_recovery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_tool_outcomes.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="recover tool outcomes",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root",
    )

    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id=coordinator_instance_id,
        task_id="task-root",
        trace_id=run_id,
        agent_role_id="Coordinator",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="what roles are available")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="orch_list_available_roles",
                        args={},
                        tool_call_id="call-1",
                    ),
                    TextPart(content="calling tool"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="orch_list_available_roles",
                        tool_call_id="call-1",
                        content={"ok": True, "data": {"roles": ["time"]}},
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="Invalid arguments for tool orch_dispatch_task",
                        tool_name="orch_dispatch_task",
                        tool_call_id="call-2",
                    )
                ]
            ),
        ],
    )

    def _session_messages(sid: str) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], message_repo.get_messages_by_session(sid))

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=_session_messages,
    )
    round_item = next(item for item in rounds if item["run_id"] == run_id)

    coordinator_messages = cast(
        list[dict[str, object]], round_item["coordinator_messages"]
    )
    part_kinds = [
        cast(
            str,
            cast(
                dict[str, object],
                cast(
                    list[dict[str, object]],
                    cast(dict[str, object], message["message"])["parts"],
                )[0],
            )["part_kind"],
        )
        for message in coordinator_messages
    ]
    assert len(coordinator_messages) == 3
    assert part_kinds == ["tool-call", "tool-return", "retry-prompt"]


def test_build_session_rounds_projects_missing_tool_pairs_from_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_event_tool_pairs.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="recover event tool pairs",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root",
    )

    events: list[dict[str, object]] = [
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "pwd"},
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:01+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "C:/Users/yex/Desktop"},
                    "error": False,
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:02+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-2",
                    "args": {"command": "pwd && cd .. && pwd"},
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:03+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-2",
                    "result": {"ok": False, "error": "cd failed"},
                    "error": True,
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]], round_item["coordinator_messages"]
    )
    parts = [
        cast(
            dict[str, object],
            cast(
                list[dict[str, object]],
                cast(dict[str, object], message["message"])["parts"],
            )[0],
        )
        for message in coordinator_messages
    ]

    assert [part["part_kind"] for part in parts] == [
        "tool-call",
        "tool-return",
        "tool-call",
        "tool-return",
    ]
    assert [part["tool_call_id"] for part in parts] == [
        "call-1",
        "call-1",
        "call-2",
        "call-2",
    ]
    assert parts[3]["is_error"] is True


def test_build_session_rounds_projects_stopped_spawn_subagent_call_without_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_stopped_spawn_subagent_call.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="call a subagent then stop",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.STOPPED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root",
    )

    events: list[dict[str, object]] = [
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-05-09T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "spawn_subagent",
                    "tool_call_id": "call-subagent",
                    "args": {
                        "role_id": "Explorer",
                        "description": "Inspect the issue",
                        "prompt": "Find the cause.",
                        "background": False,
                    },
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-05-09T10:00:01+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-shell",
                    "args": {"command": "pwd"},
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.RUN_STOPPED.value,
            "occurred_at": "2026-05-09T10:00:02+00:00",
            "payload_json": json.dumps({"reason": "stopped_by_user"}),
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]], round_item["coordinator_messages"]
    )
    parts = [
        cast(
            dict[str, object],
            cast(
                list[dict[str, object]],
                cast(dict[str, object], message["message"])["parts"],
            )[0],
        )
        for message in coordinator_messages
    ]

    assert [part["part_kind"] for part in parts] == ["tool-call"]
    assert parts[0]["tool_name"] == "spawn_subagent"
    assert parts[0]["tool_call_id"] == "call-subagent"
    assert cast(dict[str, object], parts[0]["args"])["role_id"] == "Explorer"


def test_build_session_rounds_keeps_event_tool_pairs_scoped_by_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_event_tool_pair_collisions.db"
    session_id = "session-1"
    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    for run_id, command in (("run-1", "pwd"), ("run-2", "date")):
        task_id = f"task-{run_id}"
        _ = task_repo.create(
            TaskEnvelope(
                task_id=task_id,
                session_id=session_id,
                parent_task_id=None,
                trace_id=run_id,
                role_id="Coordinator",
                objective=command,
                verification=VerificationPlan(checklist=("non_empty_response",)),
            )
        )
        agent_repo.upsert_instance(
            run_id=run_id,
            trace_id=run_id,
            session_id=session_id,
            instance_id=f"inst-{run_id}",
            role_id="Coordinator",
            workspace_id="default",
            status=InstanceStatus.COMPLETED,
        )
        run_runtime_repo.ensure(
            run_id=run_id,
            session_id=session_id,
            root_task_id=task_id,
        )

    events: list[dict[str, object]] = [
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-run-1",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "pwd"},
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-1",
                }
            ),
        },
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-run-1",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:00.500000+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "subagent output"},
                    "error": False,
                    "role_id": "Worker",
                    "instance_id": "inst-worker-1",
                }
            ),
        },
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-worker-1",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:00.250000+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "echo worker"},
                    "role_id": "Worker",
                    "instance_id": "inst-worker-1",
                }
            ),
        },
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-run-1",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:01+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "C:/Users/yex/Desktop"},
                    "error": False,
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-1",
                }
            ),
        },
        {
            "trace_id": "run-2",
            "run_id": "run-2",
            "task_id": "task-run-2",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:02+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-2",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "date"},
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-2",
                }
            ),
        },
        {
            "trace_id": "run-2",
            "run_id": "run-2",
            "task_id": "task-run-2",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:03+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-2",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "Wed Apr 29"},
                    "error": False,
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-2",
                }
            ),
        },
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-run-1",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:04+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "whoami"},
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-1",
                }
            ),
        },
        {
            "trace_id": "run-1",
            "run_id": "run-1",
            "task_id": "task-run-1",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "2026-04-29T10:00:05+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": "run-1",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "yex"},
                    "error": False,
                    "role_id": "Coordinator",
                    "instance_id": "inst-run-1",
                }
            ),
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _session_id: [],
        get_session_events=lambda _session_id: events,
    )

    by_run = {str(item["run_id"]): item for item in rounds}
    run_1_messages = cast(
        list[dict[str, object]], by_run["run-1"]["coordinator_messages"]
    )
    run_2_messages = cast(
        list[dict[str, object]], by_run["run-2"]["coordinator_messages"]
    )
    run_1_parts = [
        cast(
            dict[str, object],
            cast(
                list[dict[str, object]],
                cast(dict[str, object], message["message"])["parts"],
            )[0],
        )
        for message in run_1_messages
    ]
    run_2_parts = [
        cast(
            dict[str, object],
            cast(
                list[dict[str, object]],
                cast(dict[str, object], message["message"])["parts"],
            )[0],
        )
        for message in run_2_messages
    ]

    assert len(run_1_parts) == 4
    assert cast(dict[str, object], run_1_parts[0]["args"])["command"] == "pwd"
    assert cast(dict[str, object], run_1_parts[1]["content"])["data"] == (
        "C:/Users/yex/Desktop"
    )
    assert cast(dict[str, object], run_1_parts[2]["args"])["command"] == "whoami"
    assert cast(dict[str, object], run_1_parts[3]["content"])["data"] == "yex"
    assert cast(dict[str, object], run_2_parts[0]["args"])["command"] == "date"
    assert cast(dict[str, object], run_2_parts[1]["content"])["data"] == "Wed Apr 29"


def test_build_session_rounds_restores_missing_tool_return_from_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_event_tool_return_recovery.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="recover missing tool return",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root",
    )
    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id=coordinator_instance_id,
        task_id="task-root",
        trace_id=run_id,
        agent_role_id="Coordinator",
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="shell",
                        args={"command": "pwd"},
                        tool_call_id="call-1",
                    )
                ]
            )
        ],
    )
    events: list[dict[str, object]] = [
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_CALL.value,
            "occurred_at": "2026-04-29T10:00:00+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"command": "pwd"},
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
        {
            "trace_id": run_id,
            "run_id": run_id,
            "task_id": "task-root",
            "event_type": RunEventType.TOOL_RESULT.value,
            "occurred_at": "9999-04-29T10:00:01+00:00",
            "payload_json": json.dumps(
                {
                    "run_id": run_id,
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "result": {"ok": True, "data": "C:/Users/yex/Desktop"},
                    "error": False,
                    "role_id": "Coordinator",
                    "instance_id": coordinator_instance_id,
                }
            ),
        },
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(sid),
        ),
        get_session_events=lambda _session_id: events,
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]], round_item["coordinator_messages"]
    )
    parts = [
        cast(
            dict[str, object],
            cast(
                list[dict[str, object]],
                cast(dict[str, object], message["message"])["parts"],
            )[0],
        )
        for message in coordinator_messages
    ]

    assert [part["part_kind"] for part in parts] == ["tool-call", "tool-return"]
    assert cast(dict[str, object], parts[1]["content"])["data"] == (
        "C:/Users/yex/Desktop"
    )


def test_build_session_rounds_keeps_tool_return_from_mixed_media_replay_message(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_mixed_media_tool_return.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_instance_id = "inst-coordinator-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="MainAgent",
            objective="read image",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="MainAgent",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root",
    )
    media_part = {
        "kind": "media_ref",
        "asset_id": "asset-1",
        "session_id": session_id,
        "modality": "image",
        "mime_type": "image/png",
        "name": "example.png",
        "url": f"/api/sessions/{session_id}/media/asset-1/file",
    }

    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id=coordinator_instance_id,
        task_id="task-root",
        trace_id=run_id,
        agent_role_id="MainAgent",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="read docs/example.png")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "docs/example.png"},
                        tool_call_id="call-read-image",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        tool_call_id="call-read-image",
                        content={
                            "ok": True,
                            "data": {
                                "type": "image",
                                "path": "docs/example.png",
                                "content": [media_part],
                            },
                            "error": None,
                            "meta": {"tool_result_event_published": True},
                        },
                    ),
                    UserPromptPart(
                        content=[
                            "The model can inspect this image.",
                            ImageUrl(
                                url=f"/api/sessions/{session_id}/media/asset-1/file",
                                media_type="image/png",
                            ),
                        ]
                    ),
                ]
            ),
        ],
    )

    def _session_messages(sid: str) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], message_repo.get_messages_by_session(sid))

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=_session_messages,
    )
    round_item = next(item for item in rounds if item["run_id"] == run_id)

    coordinator_messages = cast(
        list[dict[str, object]], round_item["coordinator_messages"]
    )
    assert len(coordinator_messages) == 2
    tool_return_message = cast(dict[str, object], coordinator_messages[1]["message"])
    tool_return_parts = cast(list[dict[str, object]], tool_return_message["parts"])
    assert [part["part_kind"] for part in tool_return_parts] == ["tool-return"]
    tool_return_content = cast(dict[str, object], tool_return_parts[0]["content"])
    tool_return_data = cast(dict[str, object], tool_return_content["data"])
    tool_return_media = cast(list[dict[str, object]], tool_return_data["content"])
    assert tool_return_media[0]["kind"] == "media_ref"
    assert (
        tool_return_media[0]["url"] == f"/api/sessions/{session_id}/media/asset-1/file"
    )


def test_build_session_rounds_clears_stale_microcompact_badge_on_later_false_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_microcompact_clear.db"
    session_id = "session-1"
    run_id = "run-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="answer the user",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-coordinator-1",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id="inst-coordinator-1",
        task_id="task-root",
        trace_id=run_id,
        agent_role_id="Coordinator",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="remember this")]),
            ModelResponse(parts=[TextPart(content="done")]),
        ],
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]], message_repo.get_messages_by_session(sid)
        ),
        get_session_events=lambda _sid: [
            {
                "event_type": "model_step_started",
                "trace_id": run_id,
                "payload_json": json.dumps(
                    {
                        "role_id": "Coordinator",
                        "instance_id": "inst-coordinator-1",
                        "microcompact_applied": True,
                        "estimated_tokens_before_microcompact": 100,
                        "estimated_tokens_after_microcompact": 20,
                        "microcompact_compacted_message_count": 1,
                        "microcompact_compacted_part_count": 2,
                    }
                ),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            },
            {
                "event_type": "model_step_finished",
                "trace_id": run_id,
                "payload_json": json.dumps(
                    {
                        "role_id": "Coordinator",
                        "instance_id": "inst-coordinator-1",
                        "microcompact_applied": False,
                        "estimated_tokens_before_microcompact": 0,
                        "estimated_tokens_after_microcompact": 0,
                        "microcompact_compacted_message_count": 0,
                        "microcompact_compacted_part_count": 0,
                    }
                ),
                "occurred_at": "2026-03-25T09:32:00+00:00",
            },
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)

    assert round_item["microcompact"] is None


@pytest.mark.timeout(5)
def test_build_session_rounds_preserves_background_task_notification_intent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_background_task_intent.db"
    session_id = "session-1"
    run_id = "run-background"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-background",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective=(
                "A managed background task finished. Respond to the user with one "
                "short status update based on the notification below.\n\n"
                "<background-task-notification>\n"
                "<status>completed</status>\n"
                "</background-task-notification>"
            ),
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-coordinator-background",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    run_runtime_repo.ensure(
        run_id=run_id,
        session_id=session_id,
        root_task_id="task-root-background",
    )

    def _session_messages(sid: str) -> list[dict[str, object]]:
        return cast(list[dict[str, object]], message_repo.get_messages_by_session(sid))

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=_session_messages,
    )
    round_item = next(item for item in rounds if item["run_id"] == run_id)

    intent = str(round_item["intent"] or "")
    assert "Respond to the user with one short status update" in intent
    assert "<background-task-notification>" in intent
    assert "<status>completed</status>" in intent
    assert "Background task completed" not in intent


def test_build_session_rounds_reconstructs_completed_output_and_marks_clear_boundary(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_reconstructed_output.db"
    session_id = "session-1"
    run_old_id = "run-old"
    run_new_id = "run-new"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-old",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_old_id,
            role_id="Coordinator",
            objective="old objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-new",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_new_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_new_created_at = "2099-01-01T00:00:01+00:00"
    clear_marker_created_at = "2099-01-01T00:00:00+00:00"
    task_repo._conn.execute(
        "UPDATE tasks SET created_at=?, updated_at=? WHERE task_id=?",
        (
            task_new_created_at,
            task_new_created_at,
            "task-root-new",
        ),
    )
    task_repo._conn.commit()

    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id="inst-coordinator",
        task_id="task-root-old",
        trace_id=run_old_id,
        agent_role_id="Coordinator",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="show old result")]),
            ModelResponse(parts=[TextPart(content="historical output")]),
        ],
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(sid, include_cleared=True),
        ),
        get_session_history_markers=lambda _sid: [
            {
                "marker_id": "marker-clear-1",
                "marker_type": "clear",
                "created_at": clear_marker_created_at,
            }
        ],
        get_session_events=lambda _sid: [
            {
                "event_type": "run_completed",
                "trace_id": run_new_id,
                "payload_json": json.dumps(
                    {
                        "trace_id": run_new_id,
                        "root_task_id": "task-root-new",
                        "status": "completed",
                        "output": "reconstructed final output",
                    }
                ),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_new = next(item for item in rounds if item["run_id"] == run_new_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_new["coordinator_messages"],
    )
    reconstructed_message = cast(dict[str, object], coordinator_messages[0]["message"])
    parts = cast(list[dict[str, object]], reconstructed_message["parts"])

    assert round_new["clear_marker_before"] == {
        "marker_id": "marker-clear-1",
        "marker_type": "clear",
        "created_at": clear_marker_created_at,
        "label": "History cleared",
    }
    assert round_new["primary_role_id"] == "Coordinator"
    assert round_new["has_final_output"] is True
    assert coordinator_messages[0]["reconstructed"] is True
    assert parts[0]["content"] == "reconstructed final output"


def test_build_session_rounds_reconstructs_structured_completed_output(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_structured_output.db"
    session_id = "session-1"
    run_id = "run-structured"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-structured",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(sid, include_cleared=True),
        ),
        get_session_events=lambda _sid: [
            {
                "event_type": "run_completed",
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id="task-root-structured",
                    status="completed",
                    output=content_parts_from_text("structured reconstructed output"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )
    reconstructed_message = cast(dict[str, object], coordinator_messages[0]["message"])
    parts = cast(list[dict[str, object]], reconstructed_message["parts"])

    assert round_item["has_final_output"] is True
    assert coordinator_messages[0]["reconstructed"] is True
    assert parts[0]["content"] == "structured reconstructed output"


def test_build_session_rounds_appends_completed_output_to_existing_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_appended_output.db"
    session_id = "session-1"
    run_id = "run-with-history"
    task_id = "task-root-with-history"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    history_messages = [
        _assistant_history_message(
            run_id=run_id,
            task_id=task_id,
            created_at="2026-03-25T09:30:00+00:00",
            parts=[
                {
                    "part_kind": "thinking",
                    "part_index": 0,
                    "content": "checking files",
                },
                {
                    "part_kind": "tool-call",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"cmd": "date"},
                },
            ],
        )
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _sid: history_messages,
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_COMPLETED.value,
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id=task_id,
                    status="completed",
                    output=content_parts_from_text("terminal final answer"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )
    reconstructed_message = cast(dict[str, object], coordinator_messages[1]["message"])
    parts = cast(list[dict[str, object]], reconstructed_message["parts"])

    assert round_item["has_final_output"] is True
    assert len(coordinator_messages) == 2
    assert coordinator_messages[1]["reconstructed"] is True
    assert parts[0]["content"] == "terminal final answer"


def test_build_session_rounds_dedupes_completed_output_from_existing_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_deduped_output.db"
    session_id = "session-1"
    run_id = "run-with-persisted-final"
    task_id = "task-root-with-persisted-final"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    history_messages = [
        _assistant_history_message(
            run_id=run_id,
            task_id=task_id,
            created_at="2026-03-25T09:30:00+00:00",
            parts=[
                {
                    "part_kind": "text",
                    "content": "terminal final answer",
                }
            ],
        )
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _sid: history_messages,
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_COMPLETED.value,
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id=task_id,
                    status="completed",
                    output=content_parts_from_text("terminal final answer"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )

    assert round_item["has_final_output"] is True
    assert len(coordinator_messages) == 1
    assert "reconstructed" not in coordinator_messages[0]


def test_build_session_rounds_dedupes_completed_output_from_legacy_content(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_legacy_content_dedupe.db"
    session_id = "session-1"
    run_id = "run-with-legacy-final"
    task_id = "task-root-with-legacy-final"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    history_messages = [
        {
            "conversation_id": "conv-session-1-Coordinator",
            "agent_role_id": "Coordinator",
            "instance_id": "inst-coordinator",
            "task_id": task_id,
            "trace_id": run_id,
            "role": "assistant",
            "role_id": "Coordinator",
            "created_at": "2026-03-25T09:30:00+00:00",
            "message": {"content": "terminal final answer"},
        }
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _sid: history_messages,
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_COMPLETED.value,
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id=task_id,
                    status="completed",
                    output=content_parts_from_text("terminal final answer"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )

    assert round_item["has_final_output"] is True
    assert len(coordinator_messages) == 1
    assert "reconstructed" not in coordinator_messages[0]


def test_build_session_rounds_projects_failed_assistant_response_output_as_final(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_failed_final_output.db"
    session_id = "session-1"
    run_id = "run-failed-final"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-failed-final",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    event: dict[str, object] = {
        "event_type": RunEventType.RUN_FAILED.value,
        "trace_id": run_id,
        "payload_json": RunResult(
            trace_id=run_id,
            root_task_id="task-root-failed-final",
            status="failed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
            output=content_parts_from_text("failed final output"),
        ).model_dump_json(),
        "occurred_at": "2026-03-25T09:31:00+00:00",
    }

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(sid, include_cleared=True),
        ),
        get_session_events=lambda _sid: [event],
    )
    timeline_rounds = build_session_timeline_rounds(
        session_id=session_id,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_user_messages=lambda _sid: [],
        get_session_events=lambda _sid: [event],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    timeline_item = next(item for item in timeline_rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )
    reconstructed_message = cast(dict[str, object], coordinator_messages[0]["message"])
    parts = cast(list[dict[str, object]], reconstructed_message["parts"])

    assert round_item["has_final_output"] is True
    assert timeline_item["has_final_output"] is True
    assert coordinator_messages[0]["reconstructed"] is True
    assert parts[0]["content"] == "failed final output"


def test_build_session_rounds_appends_failed_assistant_response_to_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_failed_appended_output.db"
    session_id = "session-1"
    run_id = "run-failed-with-history"
    task_id = "task-root-failed-with-history"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    history_messages = [
        _assistant_history_message(
            run_id=run_id,
            task_id=task_id,
            created_at="2026-03-25T09:30:00+00:00",
            parts=[
                {
                    "part_kind": "tool-call",
                    "tool_name": "shell",
                    "tool_call_id": "call-1",
                    "args": {"cmd": "date"},
                }
            ],
        )
    ]

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _sid: history_messages,
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_FAILED.value,
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id=task_id,
                    status="failed",
                    completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
                    output=content_parts_from_text("failed final output"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)
    coordinator_messages = cast(
        list[dict[str, object]],
        round_item["coordinator_messages"],
    )
    reconstructed_message = cast(dict[str, object], coordinator_messages[1]["message"])
    parts = cast(list[dict[str, object]], reconstructed_message["parts"])

    assert round_item["has_final_output"] is True
    assert len(coordinator_messages) == 2
    assert coordinator_messages[1]["reconstructed"] is True
    assert parts[0]["content"] == "failed final output"


def test_build_session_rounds_ignores_assistant_error_output_for_final_flag(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_failed_error_output.db"
    session_id = "session-1"
    run_id = "run-failed-error"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-failed-error",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(sid, include_cleared=True),
        ),
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_FAILED.value,
                "trace_id": run_id,
                "payload_json": RunResult(
                    trace_id=run_id,
                    root_task_id="task-root-failed-error",
                    status="failed",
                    completion_reason=RunCompletionReason.ASSISTANT_ERROR,
                    output=content_parts_from_text("assistant error output"),
                ).model_dump_json(),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)

    assert round_item["has_final_output"] is False
    assert round_item["coordinator_messages"] == []


def test_build_session_rounds_ignores_stopped_output_for_final_flag(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_stopped_output.db"
    session_id = "session-1"
    run_id = "run-stopped"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-stopped",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="new objective",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda _sid: [],
        get_session_events=lambda _sid: [
            {
                "event_type": RunEventType.RUN_STOPPED.value,
                "trace_id": run_id,
                "payload_json": json.dumps(
                    {
                        "trace_id": run_id,
                        "root_task_id": "task-root-stopped",
                        "status": "stopped",
                        "output": "stopped diagnostic output",
                    }
                ),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)

    assert round_item["has_final_output"] is False
    assert round_item["coordinator_messages"] == []


def test_build_session_rounds_marks_compaction_boundary_for_matching_conversation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_compaction_marker.db"
    session_id = "session-1"
    run_id = "run-1"
    coordinator_role_id = "Coordinator"
    conversation_id = build_conversation_id(session_id, coordinator_role_id)

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id=coordinator_role_id,
            objective="answer the user",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-coordinator-1",
        role_id=coordinator_role_id,
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id=coordinator_role_id,
        instance_id="inst-coordinator-1",
        task_id="task-root",
        trace_id=run_id,
        messages=[ModelResponse(parts=[TextPart(content="final answer")])],
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]],
            message_repo.get_messages_by_session(
                sid,
                include_cleared=True,
                include_hidden_from_context=True,
            ),
        ),
        get_session_history_markers=lambda _sid: [
            {
                "marker_id": "marker-compaction-1",
                "marker_type": "compaction",
                "created_at": "2026-03-25T09:00:00+00:00",
                "metadata": {
                    "conversation_id": conversation_id,
                    "compaction_strategy": "rolling_summary",
                },
            }
        ],
        get_session_events=lambda _sid: [],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)

    assert round_item["compaction_marker_before"] == {
        "marker_id": "marker-compaction-1",
        "marker_type": "compaction",
        "created_at": "2026-03-25T09:00:00+00:00",
        "label": "History compacted (rolling summary)",
    }


def test_build_session_rounds_projects_microcompact_runtime_badge(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "rounds_projection_microcompact.db"
    session_id = "session-1"
    run_id = "run-1"

    task_repo = TaskRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    message_repo = MessageRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id="Coordinator",
            objective="answer the user",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id="inst-coordinator-1",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    message_repo.append(
        session_id=session_id,
        workspace_id="default",
        instance_id="inst-coordinator-1",
        task_id="task-root",
        trace_id=run_id,
        agent_role_id="Coordinator",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="remember this")]),
            ModelResponse(parts=[TextPart(content="done")]),
        ],
    )

    rounds = build_session_rounds(
        session_id=session_id,
        agent_repo=agent_repo,
        task_repo=task_repo,
        approval_tickets_by_run={},
        run_runtime_repo=run_runtime_repo,
        get_session_messages=lambda sid: cast(
            list[dict[str, object]], message_repo.get_messages_by_session(sid)
        ),
        get_session_events=lambda _sid: [
            {
                "event_type": "model_step_finished",
                "trace_id": run_id,
                "payload_json": json.dumps(
                    {
                        "role_id": "Coordinator",
                        "instance_id": "inst-coordinator-1",
                        "microcompact_applied": True,
                        "estimated_tokens_before_microcompact": 139920,
                        "estimated_tokens_after_microcompact": 9009,
                        "microcompact_compacted_message_count": 1,
                        "microcompact_compacted_part_count": 3,
                    }
                ),
                "occurred_at": "2026-03-25T09:31:00+00:00",
            }
        ],
    )

    round_item = next(item for item in rounds if item["run_id"] == run_id)

    assert round_item["microcompact"] == {
        "applied": True,
        "estimated_tokens_before": 139920,
        "estimated_tokens_after": 9009,
        "compacted_message_count": 1,
        "compacted_part_count": 3,
    }
