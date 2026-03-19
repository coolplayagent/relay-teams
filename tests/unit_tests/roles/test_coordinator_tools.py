# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.roles.role_registry import RoleLoader


def test_coordinator_uses_task_tools_and_not_emit_event() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())
    coordinator = registry.get_coordinator()
    tools = set(coordinator.tools)

    assert "create_tasks" in tools
    assert "update_task" in tools
    assert "dispatch_task" in tools
    assert "list_run_tasks" not in tools
    assert "get_workflow_status" not in tools
    assert "materialize_code_shards_from_design" not in tools
    assert "manage_state" not in tools
    assert "query_task" not in tools
    assert "verify_task" not in tools
    assert "emit_event" not in tools
