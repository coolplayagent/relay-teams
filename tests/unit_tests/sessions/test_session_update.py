from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_models import SessionMetadataPatch, SessionMode
from relay_teams.gateway.feishu import (
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry, SystemRolesUnavailableError
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.workspace import WorkspaceService
from relay_teams.workspace.workspace_repository import WorkspaceRepository


def _build_service(
    db_path: Path,
    *,
    workspace_service: WorkspaceService | None = None,
) -> SessionService:
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("create_tasks", "update_task", "dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct runs.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Handle tasks.",
        )
    )
    role_registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            description="Implements changes.",
            version="1.0.0",
            tools=("read",),
            system_prompt="Implement tasks.",
        )
    )
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        role_registry=role_registry,
        workspace_service=workspace_service,
    )


def _build_workspace_service(db_path: Path) -> WorkspaceService:
    return WorkspaceService(repository=WorkspaceRepository(db_path))


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
        SessionMetadataPatch(
            title="Renamed Session",
            custom_metadata={"label": "visible-name"},
        ),
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "title": "Renamed Session",
        "label": "visible-name",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
    }
    assert updated.updated_at >= created.updated_at


def test_sync_session_metadata_replaces_internal_metadata_and_refreshes_updated_at(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_sync_metadata.db"
    service = _build_service(db_path)
    created = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={"title": "Initial Name", "label": "visible-name"},
    )

    service.sync_session_metadata(
        "session-1",
        {
            "title": "Feishu Bot - Ops",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
            "feishu_message_id": "om_2",
            "source_label": "Ops",
        },
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "title": "Feishu Bot - Ops",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
        "feishu_message_id": "om_2",
        "source_label": "Ops",
    }
    assert updated.updated_at >= created.updated_at


def test_update_session_raises_for_unknown_session(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_missing.db"
    service = _build_service(db_path)

    with pytest.raises(KeyError, match="missing-session"):
        service.update_session("missing-session", SessionMetadataPatch(title="Nope"))


def test_create_session_raises_when_main_agent_role_is_unavailable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_update_missing_main_agent.db"
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates delegated work.",
            version="1.0.0",
            tools=("create_tasks", "update_task", "dispatch_task"),
            system_prompt="Coordinate tasks.",
        )
    )
    service = SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        role_registry=role_registry,
    )

    with pytest.raises(SystemRolesUnavailableError, match="main_agent"):
        service.create_session(session_id="session-1", workspace_id="default")


def test_update_session_preserves_explicit_auto_title_source(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_auto_title.db"
    service = _build_service(db_path)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={"title": "Initial Name"},
    )

    service.update_session(
        "session-1",
        SessionMetadataPatch(
            title="Feishu Bot ? Ops",
            title_source=SESSION_TITLE_SOURCE_AUTO,
        ),
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "title": "Feishu Bot ? Ops",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
    }


def test_update_session_clears_title_source_when_title_is_removed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_update_clear_title.db"
    service = _build_service(db_path)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={
            "title": "Manual Title",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
        },
    )

    service.update_session("session-1", SessionMetadataPatch(title=None))

    updated = service.get_session("session-1")

    assert updated.metadata == {}


def test_update_session_clears_title_for_legacy_snapshot_without_title(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_update_legacy_snapshot_clear_title.db"
    service = _build_service(db_path)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={
            "title": "Feishu Bot - Ops",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
            "source_provider": "feishu",
            "feishu_chat_id": "chat-1",
            "project": "demo",
        },
    )

    service.update_session(
        "session-1",
        SessionMetadataPatch.model_validate(
            {
                "title_source": SESSION_TITLE_SOURCE_AUTO,
                "source_provider": "feishu",
                "feishu_chat_id": "chat-1",
                "project": "demo",
            }
        ),
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "source_provider": "feishu",
        "feishu_chat_id": "chat-1",
        "project": "demo",
    }


def test_update_session_replaces_only_custom_metadata_subset(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_custom_subset.db"
    service = _build_service(db_path)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        metadata={
            "title": "Initial Name",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
            "source_label": "Feishu Group",
            "label": "visible-name",
            "favorite": "yes",
        },
    )

    service.update_session(
        "session-1",
        SessionMetadataPatch(custom_metadata={"favorite": "no"}),
    )

    updated = service.get_session("session-1")

    assert updated.metadata == {
        "title": "Initial Name",
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
        "source_label": "Feishu Group",
        "favorite": "no",
    }


def test_session_metadata_patch_rejects_reserved_custom_key() -> None:
    with pytest.raises(ValidationError, match="custom_metadata key is reserved"):
        SessionMetadataPatch(custom_metadata={"source_label": "Bad"})


def test_update_session_rejects_title_source_without_title(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_title_source_only.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    with pytest.raises(ValueError, match="title_source requires title to be set"):
        service.update_session(
            "session-1",
            SessionMetadataPatch(title_source=SESSION_TITLE_SOURCE_MANUAL),
        )


def test_create_session_defaults_normal_root_role_to_main_agent(tmp_path: Path) -> None:
    db_path = tmp_path / "session_default_normal_root.db"
    service = _build_service(db_path)

    created = service.create_session(
        session_id="session-1",
        workspace_id="default",
    )

    assert created.normal_root_role_id == "MainAgent"


def test_update_session_topology_persists_normal_root_role(tmp_path: Path) -> None:
    db_path = tmp_path / "session_normal_root_update.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    updated = service.update_session_topology(
        "session-1",
        session_mode=SessionMode.NORMAL,
        normal_root_role_id="Crafter",
        orchestration_preset_id=None,
    )

    assert updated.normal_root_role_id == "Crafter"


def test_create_session_defaults_project_scope_to_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "session_project_scope.db"
    service = _build_service(db_path)

    created = service.create_session(
        session_id="session-project-1",
        workspace_id="default",
    )

    assert created.project_kind.value == "workspace"
    assert created.project_id == "default"


def test_rebind_session_workspace_updates_workspace_project_and_agents(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_rebind.db"
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
    created = service.create_session(
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

    updated = service.rebind_session_workspace(
        "session-1",
        workspace_id=target_workspace.workspace_id,
    )

    assert updated.workspace_id == target_workspace.workspace_id
    assert updated.project_id == target_workspace.workspace_id
    assert updated.updated_at >= created.updated_at
    assert (
        service.get_session("session-1").workspace_id == target_workspace.workspace_id
    )
    assert (
        agent_repo.get_instance("inst-1").workspace_id == target_workspace.workspace_id
    )


def test_rebind_session_workspace_rejects_recoverable_run(tmp_path: Path) -> None:
    db_path = tmp_path / "session_rebind_active_run.db"
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
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot rebind workspace while session has active or recoverable run",
    ):
        service.rebind_session_workspace(
            "session-1",
            workspace_id=target_workspace.workspace_id,
        )

    assert (
        service.get_session("session-1").workspace_id == default_workspace.workspace_id
    )


def test_delete_session_rejects_active_run_without_force(tmp_path: Path) -> None:
    db_path = tmp_path / "session_delete_active_run.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot delete session while it has active or recoverable run",
    ):
        service.delete_session("session-1")


def test_delete_session_rejects_related_data_without_cascade(tmp_path: Path) -> None:
    db_path = tmp_path / "session_delete_needs_cascade.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    message_repo = MessageRepository(db_path)
    _ = message_repo.append_user_prompt_if_missing(
        session_id="session-1",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        content="hello",
        workspace_id="default",
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot delete session without cascade while related session data exists",
    ):
        service.delete_session("session-1")


def test_delete_session_force_allows_active_run_cleanup(tmp_path: Path) -> None:
    db_path = tmp_path / "session_delete_force.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-1",
        session_id="session-1",
    )

    service.delete_session("session-1", force=True, cascade=True)

    with pytest.raises(KeyError, match="session-1"):
        service.get_session("session-1")
