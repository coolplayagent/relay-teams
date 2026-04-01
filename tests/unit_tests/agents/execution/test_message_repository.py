from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from typing import cast

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from agent_teams.workspace import build_conversation_id


def test_message_repo_sanitizes_stale_task_status_error_on_read(tmp_path: Path) -> None:
    db_path = tmp_path / "message_repo.db"
    repo = MessageRepository(db_path)
    repo.append(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="dispatch_task",
                        args={"task": "ask_time"},
                        tool_call_id="dispatch_task:1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="dispatch_task",
                        tool_call_id="dispatch_task:1",
                        content={"ok": True},
                    )
                ]
            ),
        ],
    )

    row = repo._conn.execute(
        "SELECT id, message_json FROM messages WHERE role='user'"
    ).fetchone()
    assert row is not None
    payload = json.loads(str(row["message_json"]))
    tool_return = payload[0]["parts"][0]["content"]
    tool_return["data"] = {
        "task_status": {
            "ask_time": {
                "task_name": "ask_time",
                "task_id": "task-1",
                "role_id": "time",
                "instance_id": "inst-1",
                "status": "completed",
                "result": "2026-03-07 00:41:29",
                "error": "Task stopped by user",
            }
        }
    }
    repo._conn.execute(
        "UPDATE messages SET message_json=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), int(row["id"])),
    )
    repo._conn.commit()

    messages = repo.get_messages_by_session("session-1")
    message = cast(dict[str, object], messages[1]["message"])
    parts = cast(list[object], message["parts"])
    part = cast(dict[str, object], parts[0])
    content = cast(dict[str, object], part["content"])
    data = cast(dict[str, object], content["data"])
    task_status_map = cast(dict[str, object], data["task_status"])
    task_status = cast(dict[str, object], task_status_map["ask_time"])
    assert task_status["status"] == "completed"
    assert task_status["result"] == "2026-03-07 00:41:29"
    assert "error" not in task_status

    history = repo.get_history("inst-1")
    history_part = history[1].parts[0]
    assert isinstance(history_part, ToolReturnPart)
    assert isinstance(history_part.content, dict)
    history_task_status = history_part.content["data"]["task_status"]["ask_time"]
    assert history_task_status["status"] == "completed"
    assert "error" not in history_task_status


def test_message_repo_hides_duplicate_task_objective_messages(tmp_path: Path) -> None:
    db_path = tmp_path / "message_repo_dedupe.db"
    repo = MessageRepository(db_path)

    for _ in range(2):
        repo.append(
            session_id="session-1",
            workspace_id="default",
            instance_id="inst-1",
            task_id="task-1",
            trace_id="run-1",
            messages=[
                ModelRequest(
                    parts=[
                        UserPromptPart(content="query time"),
                    ]
                )
            ],
        )

    messages = repo.get_messages_by_session("session-1")

    assert len(messages) == 1


def test_append_user_prompt_if_missing_dedupes_only_tail_prompt(tmp_path: Path) -> None:
    db_path = tmp_path / "message_repo_append_prompt.db"
    repo = MessageRepository(db_path)

    inserted_first = repo.append_user_prompt_if_missing(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="query time",
    )
    inserted_second = repo.append_user_prompt_if_missing(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="query time",
    )

    assert inserted_first is True
    assert inserted_second is False
    history = repo.get_history_for_task("inst-1", "task-1")
    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    assert history[0].parts[0].content == "query time"


def test_conversation_history_can_span_multiple_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "message_repo_conversation.db"
    repo = MessageRepository(db_path)
    conversation_id = build_conversation_id("session-1", "time")
    workspace_id = "default"

    repo.append(
        session_id="session-1",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="first turn")])],
    )
    repo.append(
        session_id="session-1",
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-2",
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="second turn")])],
    )

    history = repo.get_history_for_conversation(conversation_id)

    assert len(history) == 2
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[1], ModelRequest)
    assert history[0].parts[0].content == "first turn"
    assert history[1].parts[0].content == "second turn"


def test_message_repo_drops_duplicate_late_tool_return_but_keeps_user_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "message_repo_duplicate_tool_return.db"
    repo = MessageRepository(db_path)
    conversation_id = build_conversation_id("session-1", "time")

    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="write",
                        args={"content": "hello"},
                        tool_call_id="call-1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="write",
                        tool_call_id="call-1",
                        content={"ok": True},
                    )
                ]
            ),
        ],
    )
    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="write",
                        tool_call_id="call-1",
                        content={"ok": True},
                    ),
                    UserPromptPart(content="optimize it"),
                ]
            )
        ],
    )

    history = repo.get_history_for_conversation(conversation_id)

    assert len(history) == 3
    assert isinstance(history[0], ModelResponse)
    assert isinstance(history[1], ModelRequest)
    assert isinstance(history[1].parts[0], ToolReturnPart)
    assert isinstance(history[2], ModelRequest)
    assert len(history[2].parts) == 1
    assert isinstance(history[2].parts[0], UserPromptPart)
    assert history[2].parts[0].content == "optimize it"


def test_message_repo_drops_orphan_tool_return_request_from_history(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "message_repo_orphan_tool_return.db"
    repo = MessageRepository(db_path)
    conversation_id = build_conversation_id("session-1", "time")

    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="write",
                        tool_call_id="call-missing",
                        content={"ok": False},
                    )
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="continue")]),
        ],
    )

    history = repo.get_history_for_conversation(conversation_id)

    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], UserPromptPart)
    assert history[0].parts[0].content == "continue"


def test_message_repo_append_is_thread_safe_under_parallel_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "message_repo_parallel.db"
    repo = MessageRepository(db_path)

    def _write(i: int) -> None:
        repo.append(
            session_id="session-1",
            workspace_id="default",
            instance_id="inst-1",
            task_id="task-1",
            trace_id="run-1",
            messages=[
                ModelRequest(parts=[UserPromptPart(content=f"query time #{i}")]),
            ],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_write, i) for i in range(200)]
        for future in futures:
            future.result()

    row = repo._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()
    assert row is not None
    assert int(row["c"]) == 200


def test_message_repo_filters_active_segment_after_clear_marker(tmp_path: Path) -> None:
    db_path = tmp_path / "message_repo_history_markers.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "time")

    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="before clear")])],
    )
    marker_repo.create_clear_marker("session-1")
    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-2",
        trace_id="run-2",
        messages=[ModelRequest(parts=[UserPromptPart(content="after clear")])],
    )

    active_messages = repo.get_messages_by_session("session-1")
    all_messages = repo.get_messages_by_session("session-1", include_cleared=True)
    active_history = repo.get_history_for_conversation(conversation_id)

    assert len(active_messages) == 1
    active_payload = cast(dict[str, object], active_messages[0]["message"])
    active_part = cast(list[dict[str, object]], active_payload["parts"])[0]
    assert active_part["content"] == "after clear"
    assert len(all_messages) == 2
    assert len(active_history) == 1
    active_history_part = active_history[0].parts[0]
    assert isinstance(active_history_part, UserPromptPart)
    assert active_history_part.content == "after clear"


def test_compact_conversation_history_marks_messages_hidden_from_context(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "message_repo_compaction_markers.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "time")

    repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="time",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="pre-clear")])],
    )
    marker_repo.create_clear_marker("session-1")
    for index in range(3):
        repo.append(
            session_id="session-1",
            workspace_id="default",
            conversation_id=conversation_id,
            agent_role_id="time",
            instance_id="inst-1",
            task_id=f"task-{index + 2}",
            trace_id=f"run-{index + 2}",
            messages=[
                ModelRequest(parts=[UserPromptPart(content=f"post-clear-{index + 1}")])
            ],
        )

    repo.compact_conversation_history(conversation_id, keep_message_count=1)

    raw_messages = repo.get_messages_by_session(
        "session-1",
        include_cleared=True,
        include_hidden_from_context=True,
    )
    active_history = repo.get_history_for_conversation(conversation_id)

    assert len(raw_messages) == 4
    assert len(active_history) == 1
    final_history_part = active_history[0].parts[0]
    assert isinstance(final_history_part, UserPromptPart)
    assert final_history_part.content == "post-clear-3"
    hidden_messages = [
        message for message in raw_messages if message["hidden_from_context"]
    ]
    assert len(hidden_messages) == 2
    hidden_reasons = {
        cast(str, message["hidden_reason"]) for message in hidden_messages
    }
    assert hidden_reasons == {"compaction"}
