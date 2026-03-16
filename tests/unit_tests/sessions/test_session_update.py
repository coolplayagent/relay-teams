from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_service import SessionService
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


def _build_service(db_path: Path) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
    )


def test_update_session_replaces_metadata_and_refreshes_updated_at(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_update.db"
    service = _build_service(db_path)
    created = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={"title": "Initial Name"},
    )

    service.update_session(
        "session-1",
        {
            "title": "Renamed Session",
            "label": "visible-name",
        },
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "title": "Renamed Session",
        "label": "visible-name",
    }
    assert updated.updated_at >= created.updated_at


def test_update_session_raises_for_unknown_session(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_missing.db"
    service = _build_service(db_path)

    with pytest.raises(KeyError, match="missing-session"):
        service.update_session("missing-session", {"title": "Nope"})
