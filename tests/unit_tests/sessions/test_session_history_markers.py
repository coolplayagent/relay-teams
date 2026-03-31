# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.sessions.session_service import SessionService
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


def _build_service(
    db_path: Path,
) -> tuple[SessionService, MessageRepository, TokenUsageRepository, TaskRepository]:
    marker_repo = SessionHistoryMarkerRepository(db_path)
    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    token_usage_repo = TokenUsageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    task_repo = TaskRepository(db_path)
    return (
        SessionService(
            session_repo=SessionRepository(db_path),
            task_repo=task_repo,
            agent_repo=AgentInstanceRepository(db_path),
            message_repo=message_repo,
            approval_ticket_repo=ApprovalTicketRepository(db_path),
            run_runtime_repo=RunRuntimeRepository(db_path),
            event_log=EventLog(db_path),
            token_usage_repo=token_usage_repo,
            session_history_marker_repo=marker_repo,
            run_event_hub=RunEventHub(),
        ),
        message_repo,
        token_usage_repo,
        task_repo,
    )


def test_session_clear_uses_logical_history_divider(tmp_path: Path) -> None:
    db_path = tmp_path / "session_history_markers.db"
    service, message_repo, token_usage_repo, task_repo = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-old",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-old",
            role_id="Coordinator",
            objective="before clear",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conv-1",
        agent_role_id="Coordinator",
        instance_id="inst-1",
        task_id="task-old",
        trace_id="run-old",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="before clear")]),
            ModelResponse(parts=[TextPart(content="old response")]),
        ],
    )
    token_usage_repo.record(
        session_id="session-1",
        run_id="run-old",
        instance_id="inst-1",
        role_id="Coordinator",
        input_tokens=11,
        output_tokens=5,
    )

    cleared_count = service.clear_session_messages("session-1")

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-new",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-new",
            role_id="Coordinator",
            objective="after clear",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id="conv-1",
        agent_role_id="Coordinator",
        instance_id="inst-1",
        task_id="task-new",
        trace_id="run-new",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="after clear")]),
            ModelResponse(parts=[TextPart(content="new response")]),
        ],
    )
    token_usage_repo.record(
        session_id="session-1",
        run_id="run-new",
        instance_id="inst-1",
        role_id="Coordinator",
        input_tokens=7,
        output_tokens=3,
    )

    active_messages = service.get_session_messages("session-1")
    historical_messages = message_repo.get_messages_by_session(
        "session-1",
        include_cleared=True,
    )
    active_usage = service.get_token_usage_by_session("session-1")
    rounds = service.build_session_rounds("session-1")
    round_new = next(item for item in rounds if item["run_id"] == "run-new")

    assert cleared_count == 2
    assert len(active_messages) == 2
    assert len(historical_messages) == 4
    assert active_usage.total_input_tokens == 7
    assert active_usage.total_output_tokens == 3
    assert round_new["clear_marker_before"] is not None
    coordinator_messages = round_new["coordinator_messages"]
    assert isinstance(coordinator_messages, list)
    assert len(coordinator_messages) == 1


def test_session_history_marker_repository_skips_invalid_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_history_markers_invalid.db"
    repository = SessionHistoryMarkerRepository(db_path)
    _ = repository.create_clear_marker("session-1")
    _insert_history_marker_row(
        db_path,
        marker_id="None",
        session_id="session-1",
    )

    markers = repository.list_by_session("session-1")

    assert len(markers) == 1
    assert markers[0].session_id == "session-1"
    assert repository.get_latest("session-1") is not None


def _insert_history_marker_row(
    db_path: Path,
    *,
    marker_id: str,
    session_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO session_history_markers(
            marker_id,
            session_id,
            marker_type,
            metadata_json,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            marker_id,
            session_id,
            "clear",
            "{}",
            now,
        ),
    )
    connection.commit()
    connection.close()
