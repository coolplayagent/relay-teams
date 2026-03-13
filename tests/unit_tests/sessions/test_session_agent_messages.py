from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import ModelRequest, UserPromptPart

from agent_teams.agents.enums import InstanceStatus
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.service import SessionService
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_repo import SessionRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository


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
    assert messages[0]["role_id"] == "time"
