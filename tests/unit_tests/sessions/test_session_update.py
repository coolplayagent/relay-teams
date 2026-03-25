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
from agent_teams.sessions.session_models import SessionMode
from agent_teams.feishu import (
    SESSION_METADATA_TITLE_SOURCE_KEY,
    SESSION_TITLE_SOURCE_AUTO,
    SESSION_TITLE_SOURCE_MANUAL,
)
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


def _build_service(db_path: Path) -> SessionService:
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
        SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_MANUAL,
    }
    assert updated.updated_at >= created.updated_at


def test_update_session_raises_for_unknown_session(tmp_path: Path) -> None:
    db_path = tmp_path / "session_update_missing.db"
    service = _build_service(db_path)

    with pytest.raises(KeyError, match="missing-session"):
        service.update_session("missing-session", {"title": "Nope"})


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
        {
            "title": "Feishu Bot ? Ops",
            SESSION_METADATA_TITLE_SOURCE_KEY: SESSION_TITLE_SOURCE_AUTO,
        },
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

    service.update_session("session-1", {})

    updated = service.get_session("session-1")

    assert updated.metadata == {}


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
