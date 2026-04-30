# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.runtime_tools import runtime_tools_for_role


def test_runtime_tools_keep_orchestration_tools_for_coordinator() -> None:
    registry = RoleRegistry()
    coordinator = RoleDefinition(
        role_id="Coordinator",
        name="Coordinator",
        description="Coordinates work.",
        version="1",
        tools=("orch_create_tasks", "orch_dispatch_task"),
        system_prompt="Coordinate.",
    )
    registry.register(coordinator)

    assert runtime_tools_for_role(
        role_registry=registry,
        role=coordinator,
        consumer="test",
    ) == ("orch_create_tasks", "orch_dispatch_task")


def test_runtime_tools_filter_orchestration_tools_for_non_coordinator() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates work.",
            version="1",
            tools=("orch_create_tasks", "orch_update_task", "orch_dispatch_task"),
            system_prompt="Coordinate.",
        )
    )
    crafter = RoleDefinition(
        role_id="Crafter",
        name="Crafter",
        description="Builds work.",
        version="1",
        tools=("read", "orch_dispatch_task", "shell"),
        system_prompt="Build.",
    )

    assert runtime_tools_for_role(
        role_registry=registry,
        role=crafter,
        consumer="test",
    ) == ("read", "shell")
