# -*- coding: utf-8 -*-
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


@pytest.mark.asyncio
async def test_runtime_role_resolver_async_creates_role_from_template(
    tmp_path: Path,
) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )

    role = await resolver.create_temporary_role_async(
        run_id="run-1",
        session_id="session-1",
        role=TemporaryRoleSpec(
            role_id="tmp_async_researcher",
            name="Tmp Async Researcher",
            description="temporary",
            system_prompt="tmp",
            template_role_id="Analyst",
        ),
    )

    assert role.role_id == "tmp_async_researcher"
    assert role.tools == ("read", "office_read_markdown")
    assert role.model_profile == "default"
    assert await resolver.list_temporary_role_ids_async(run_id="run-1") == (
        "tmp_async_researcher",
    )
    await resolver.delete_temporary_role_async(
        run_id="run-1",
        role_id="tmp_async_researcher",
    )
    assert await resolver.list_temporary_role_ids_async(run_id="run-1") == ()


@pytest.mark.asyncio
async def test_runtime_role_resolver_async_rejects_reserved_ids_and_templates(
    tmp_path: Path,
) -> None:
    resolver = RuntimeRoleResolver(
        role_registry=_base_registry(),
        temporary_role_repository=TemporaryRoleRepository(tmp_path / "roles.db"),
    )

    with pytest.raises(ValueError, match="coordinator role"):
        await resolver.create_temporary_role_async(
            run_id="run-1",
            session_id="session-1",
            role=TemporaryRoleSpec(
                role_id="Coordinator",
                name="Bad",
                description="bad",
                system_prompt="bad",
            ),
        )
    with pytest.raises(ValueError, match="main agent role"):
        await resolver.create_temporary_role_async(
            run_id="run-1",
            session_id="session-1",
            role=TemporaryRoleSpec(
                role_id="MainAgent",
                name="Bad",
                description="bad",
                system_prompt="bad",
            ),
        )
    with pytest.raises(
        ValueError,
        match="Coordinator role cannot be used as a temporary role template",
    ):
        await resolver.create_temporary_role_async(
            run_id="run-1",
            session_id="session-1",
            role=TemporaryRoleSpec(
                role_id="tmp_async_dispatch",
                name="Tmp Async Dispatch",
                description="temporary",
                system_prompt="tmp",
                template_role_id="Coordinator",
            ),
        )
