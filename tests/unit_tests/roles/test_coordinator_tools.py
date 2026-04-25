# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.roles.role_registry import RoleLoader


def test_coordinator_uses_task_tools_and_not_emit_event() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())
    coordinator = registry.get_coordinator()
    tools = set(coordinator.tools)

    assert tools == {
        "orch_create_tasks",
        "orch_create_temporary_role",
        "list_skill_roles",
        "activate_skill_roles",
        "orch_update_task",
        "orch_list_available_roles",
        "orch_list_delegated_tasks",
        "orch_dispatch_task",
    }
