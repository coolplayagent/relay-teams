from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.session_service import SessionService
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerType,
)
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.workspace import build_conversation_id


def _build_service(db_path: Path) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=RunEventHub(),
    )


def test_get_agent_messages_includes_role_id(tmp_path: Path) -> None:
    db_path = tmp_path / "session_agent_messages.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="time",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )

    message_repo = MessageRepository(db_path)
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[ModelRequest(parts=[UserPromptPart(content="what time is it?")])],
    )

    messages = service.get_agent_messages("session-1", "inst-1")

    assert len(messages) == 1
    assert messages[0]["entry_type"] == "message"
    assert messages[0]["role_id"] == "time"


def test_get_agent_messages_returns_hidden_history_and_compaction_marker(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_agent_messages_markers.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    service = SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(
            db_path,
            session_history_marker_repo=marker_repo,
        ),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        event_log=EventLog(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        session_history_marker_repo=marker_repo,
        run_event_hub=RunEventHub(),
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )

    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")
    for index in range(3):
        message_repo.append(
            session_id="session-1",
            workspace_id="default",
            conversation_id=conversation_id,
            agent_role_id="writer",
            instance_id="inst-1",
            task_id=f"task-{index + 1}",
            trace_id="run-1",
            messages=[
                ModelRequest(parts=[UserPromptPart(content=f"turn-{index + 1}")]),
            ],
        )
    marker = marker_repo.create(
        session_id="session-1",
        marker_type=SessionHistoryMarkerType.COMPACTION,
        metadata={
            "conversation_id": conversation_id,
            "role_id": "writer",
            "summary_markdown": "summary",
        },
    )
    hidden_count = message_repo.hide_conversation_messages_for_compaction(
        conversation_id=conversation_id,
        hide_message_count=2,
        hidden_marker_id=marker.marker_id,
    )

    timeline = service.get_agent_messages("session-1", "inst-1")

    assert hidden_count == 2
    assert [entry["entry_type"] for entry in timeline] == [
        "message",
        "message",
        "marker",
        "message",
    ]
    assert timeline[0]["hidden_from_context"] is True
    assert timeline[1]["hidden_from_context"] is True
    assert timeline[2]["marker_type"] == "compaction"
    assert timeline[3]["hidden_from_context"] is False
