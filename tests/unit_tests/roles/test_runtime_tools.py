# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.roles.role_contracts import (
    RoleContract,
    RoleContractInvariant,
    RoleContractInvariantType,
)
from relay_teams.roles.runtime_tools import (
    role_with_runtime_tools,
    runtime_denied_tools_for_role,
    runtime_tools_for_role,
    strip_coordinator_only_tools,
    strip_contract_denied_tools,
)


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


def test_role_with_runtime_tools_returns_same_role_when_tools_are_unchanged() -> None:
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

    role = role_with_runtime_tools(
        role_registry=registry,
        role=coordinator,
        consumer="test",
    )

    assert role is coordinator


def test_role_with_runtime_tools_returns_filtered_role_for_non_coordinator() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates work.",
            version="1",
            tools=("orch_dispatch_task",),
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

    role = role_with_runtime_tools(
        role_registry=registry,
        role=crafter,
        consumer="test",
    )

    assert role is not crafter
    assert role.tools == ("read", "shell")


def test_strip_coordinator_only_tools_removes_orchestration_tools() -> None:
    assert strip_coordinator_only_tools(("read", "orch_dispatch_task", "shell")) == (
        "read",
        "shell",
    )


def test_runtime_tools_filter_contract_denied_tools() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            description="Coordinates work.",
            version="1",
            tools=("orch_dispatch_task",),
            system_prompt="Coordinate.",
        )
    )
    reviewer = RoleDefinition(
        role_id="Reviewer",
        name="Reviewer",
        description="Reviews work.",
        version="1",
        tools=("read", "write_tmp", "shell"),
        contract=RoleContract(
            invariants=(
                RoleContractInvariant(
                    invariant=RoleContractInvariantType.MUST_NOT_HAVE_TOOLS,
                    tools=("write_tmp", "shell"),
                ),
            ),
        ),
        system_prompt="Review.",
    )

    assert runtime_denied_tools_for_role(reviewer) == ("write_tmp", "shell")
    assert strip_contract_denied_tools(
        role=reviewer,
        tools=reviewer.tools,
        consumer="test",
    ) == ("read",)
    assert runtime_tools_for_role(
        role_registry=registry,
        role=reviewer,
        consumer="test",
    ) == ("read",)
