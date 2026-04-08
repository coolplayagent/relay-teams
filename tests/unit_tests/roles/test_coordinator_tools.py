# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.roles.role_registry import RoleLoader


def test_coordinator_uses_task_tools_and_not_emit_event() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())
    coordinator = registry.get_coordinator()
    tools = set(coordinator.tools)

    assert tools == {
        "create_tasks",
        "create_temporary_role",
        "update_task",
        "list_available_roles",
        "list_delegated_tasks",
        "dispatch_task",
    }
