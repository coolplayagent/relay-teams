from __future__ import annotations

from pathlib import Path

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.service import SessionService
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.sessions.session_repo import SessionRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


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
        role_memory_service=RoleMemoryService(
            repository=RoleMemoryRepository(db_path),
        ),
    )


def test_session_service_updates_and_deletes_agent_reflection(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_reflection_memory.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id="default",
        status=InstanceStatus.IDLE,
    )

    updated = service.update_agent_reflection(
        "session-1",
        "inst-1",
        summary="- Prefer concise implementation notes",
    )

    assert updated["summary"] == "- Prefer concise implementation notes"
    assert updated["preview"] == "- Prefer concise implementation notes"
    assert updated["source"] == "manual_edit"
    assert updated["updated_at"] is not None

    stored = service.get_agent_reflection("session-1", "inst-1")
    assert stored["summary"] == "- Prefer concise implementation notes"
    assert stored["updated_at"] is not None

    deleted = service.delete_agent_reflection("session-1", "inst-1")
    assert deleted == {
        "instance_id": "inst-1",
        "role_id": "writer",
        "summary": "",
        "preview": "",
        "updated_at": None,
        "source": "manual_delete",
    }

    empty = service.get_agent_reflection("session-1", "inst-1")
    assert empty == {
        "instance_id": "inst-1",
        "role_id": "writer",
        "summary": "",
        "preview": "",
        "updated_at": None,
        "source": "stored",
    }
