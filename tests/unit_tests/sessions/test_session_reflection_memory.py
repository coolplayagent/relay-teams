from __future__ import annotations

from pathlib import Path

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_service import SessionService
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.roles.memory_repository import RoleMemoryRepository
from agent_teams.roles.memory_service import RoleMemoryService
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.workspace import WorkspaceService
from agent_teams.workspace.workspace_repository import WorkspaceRepository


def _build_service(
    db_path: Path,
    *,
    workspace_service: WorkspaceService | None = None,
) -> SessionService:
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
        workspace_service=workspace_service,
        role_memory_service=RoleMemoryService(
            repository=RoleMemoryRepository(db_path),
        ),
    )


def _build_workspace_service(db_path: Path) -> WorkspaceService:
    return WorkspaceService(repository=WorkspaceRepository(db_path))


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


def test_rebind_session_workspace_keeps_old_role_memory_without_migration(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_reflection_rebind.db"
    workspace_service = _build_workspace_service(db_path)
    default_root = tmp_path / "workspace-default"
    target_root = tmp_path / "workspace-target"
    default_root.mkdir()
    target_root.mkdir()
    default_workspace = workspace_service.create_workspace_for_root(
        root_path=default_root
    )
    target_workspace = workspace_service.create_workspace_for_root(
        root_path=target_root
    )
    service = _build_service(
        db_path,
        workspace_service=workspace_service,
    )
    _ = service.create_session(
        session_id="session-1",
        workspace_id=default_workspace.workspace_id,
    )

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        workspace_id=default_workspace.workspace_id,
        status=InstanceStatus.IDLE,
    )
    _ = service.update_agent_reflection(
        "session-1",
        "inst-1",
        summary="- Keep the old workspace memory",
    )

    updated = service.rebind_session_workspace(
        "session-1",
        workspace_id=target_workspace.workspace_id,
    )

    assert updated.workspace_id == target_workspace.workspace_id
    assert (
        agent_repo.get_instance("inst-1").workspace_id == target_workspace.workspace_id
    )
    assert service.get_agent_reflection("session-1", "inst-1") == {
        "instance_id": "inst-1",
        "role_id": "writer",
        "summary": "",
        "preview": "",
        "updated_at": None,
        "source": "stored",
    }
    role_memory_repo = RoleMemoryRepository(db_path)
    assert (
        role_memory_repo.read_role_memory(
            role_id="writer",
            workspace_id=default_workspace.workspace_id,
        ).content_markdown
        == "- Keep the old workspace memory"
    )
    assert (
        role_memory_repo.read_role_memory(
            role_id="writer",
            workspace_id=target_workspace.workspace_id,
        ).content_markdown
        == ""
    )
