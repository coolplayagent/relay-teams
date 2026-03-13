from __future__ import annotations

from pathlib import Path

from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.service import SessionService
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


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


def test_session_rounds_include_persisted_run_state_overlay(tmp_path: Path) -> None:
    db_path = tmp_path / "round_state_overlay.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1")

    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-1",
            objective="do work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    run_runtime_repo = RunRuntimeRepository(db_path)
    _ = run_runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    page = service.get_session_rounds("session-1", limit=8)
    items = page.get("items")
    assert isinstance(items, list)
    assert len(items) == 1
    first = items[0]
    assert isinstance(first, dict)
    assert first.get("run_status") == "running"
    assert first.get("run_phase") == "running"
    assert first.get("is_recoverable") is True
