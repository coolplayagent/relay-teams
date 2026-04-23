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

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.sessions.session_rounds_projection import build_session_rounds
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.run_models import RunResult
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.workspace import build_conversation_id


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
        "name": "relay_teams.png",
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
            ModelRequest(parts=[UserPromptPart(content="read docs/relay_teams.png")]),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read",
                        args={"path": "docs/relay_teams.png"},
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
                                "path": "docs/relay_teams.png",
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

    assert coordinator_messages[0]["reconstructed"] is True
    assert parts[0]["content"] == "structured reconstructed output"


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
