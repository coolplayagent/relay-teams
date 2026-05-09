# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.roles.role_models import RoleMode
from relay_teams.roles.role_registry import RoleLoader


def test_builtin_delegation_planner_is_planning_only_subagent() -> None:
    role = RoleLoader().load_one(get_builtin_roles_dir() / "delegation_planner.md")

    assert role.role_id == "DelegationPlanner"
    assert role.mode == RoleMode.SUBAGENT
    forbidden_tools = {
        "orch_create_tasks",
        "orch_create_temporary_role",
        "orch_update_task",
        "orch_list_available_roles",
        "orch_list_delegated_tasks",
        "orch_dispatch_task",
        "edit",
        "write",
        "shell",
    }
    assert not forbidden_tools.intersection(role.tools)
