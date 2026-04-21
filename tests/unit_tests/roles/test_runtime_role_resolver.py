from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import TemporaryRoleSpec
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository


def _base_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="system",
            version="1",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="coord",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="MainAgent",
            description="system",
            version="1",
            tools=("read",),
            system_prompt="main",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Analyst",
            name="Analyst",
            description="static",
            version="1",
            tools=("read",),
            system_prompt="analyst",
        )
    )
    return registry


def test_runtime_role_resolver_prefers_run_temporary_roles(tmp_path: Path) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )
    resolver.create_temporary_role(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_writer",
            name="Tmp Writer",
            description="temporary",
            system_prompt="tmp",
            tools=("write",),
        ),
    )

    role = resolver.get_effective_role(run_id="run-1", role_id="tmp_writer")
    assert role.role_id == "tmp_writer"
    assert role.tools == ("write", "office_read_markdown")


def test_runtime_role_resolver_rejects_reserved_ids(tmp_path: Path) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )

    with pytest.raises(ValueError):
        resolver.create_temporary_role(
            run_id="run-1",
            session_id="session-1",
            role=TemporaryRoleSpec(
                role_id="Coordinator",
                name="Bad",
                description="bad",
                system_prompt="bad",
            ),
        )


def test_runtime_role_resolver_applies_template_defaults(tmp_path: Path) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )

    resolver.create_temporary_role(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_researcher",
            name="Tmp Researcher",
            description="temporary",
            system_prompt="tmp",
            template_role_id="Analyst",
        ),
    )

    role = resolver.get_effective_role(run_id="run-1", role_id="tmp_researcher")
    assert role.tools == ("read", "office_read_markdown")
    assert role.model_profile == "default"


def test_runtime_role_resolver_does_not_add_office_tool_to_coordinator_like_role(
    tmp_path: Path,
) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )

    resolver.create_temporary_role(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="dispatch_lead",
            name="Dispatch Lead",
            description="temporary coordinator",
            system_prompt="coordinate",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
        ),
    )

    role = resolver.get_effective_role(run_id="run-1", role_id="dispatch_lead")
    assert role.tools == ("orch_create_tasks", "orch_update_task", "orch_dispatch_task")
