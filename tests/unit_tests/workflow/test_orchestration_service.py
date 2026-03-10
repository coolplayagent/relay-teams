# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleRegistry
from agent_teams.workflow.orchestration_service import (
    _validate_roles_exist,
    _validate_task_dependencies,
)
from agent_teams.workflow.spec import WorkflowTaskSpec


def _role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="spec_coder",
            name="Spec Coder",
            version="1.0.0",
            tools=(),
            mcp_servers=(),
            skills=(),
            model_profile="default",
            system_prompt="Write code.",
        )
    )
    return registry


def test_validate_roles_exist_allows_single_role_task_graph() -> None:
    tasks = [
        WorkflowTaskSpec(
            task_name="code",
            objective="Write hello.py",
            role_id="spec_coder",
            depends_on=(),
        )
    ]

    _validate_roles_exist(_role_registry(), tasks)


def test_validate_task_dependencies_rejects_missing_task_reference() -> None:
    tasks = [
        WorkflowTaskSpec(
            task_name="verify",
            objective="Verify output",
            role_id="spec_coder",
            depends_on=("code",),
        )
    ]

    with pytest.raises(ValueError, match="depends on missing task 'code'"):
        _validate_task_dependencies(tasks)
