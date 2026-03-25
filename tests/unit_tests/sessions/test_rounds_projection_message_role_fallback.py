from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.sessions.session_rounds_projection import build_session_rounds
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


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
        role_id="coordinator_agent",
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
    assert coordinator_messages[0].get("role_id") == "coordinator_agent"


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
            role_id="coordinator_agent",
            objective="recover tool outcomes",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id=run_id,
        trace_id=run_id,
        session_id=session_id,
        instance_id=coordinator_instance_id,
        role_id="coordinator_agent",
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
        agent_role_id="coordinator_agent",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="what roles are available")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="list_available_roles",
                        args={},
                        tool_call_id="call-1",
                    ),
                    TextPart(content="calling tool"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="list_available_roles",
                        tool_call_id="call-1",
                        content={"ok": True, "data": {"roles": ["time"]}},
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="Invalid arguments for tool dispatch_task",
                        tool_name="dispatch_task",
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
            role_id="coordinator_agent",
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
            role_id="coordinator_agent",
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
        agent_role_id="coordinator_agent",
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
    assert coordinator_messages[0]["reconstructed"] is True
    assert parts[0]["content"] == "reconstructed final output"
