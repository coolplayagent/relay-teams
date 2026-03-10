# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.roles.registry import RoleLoader


def test_coordinator_uses_workflow_tools_and_not_emit_event() -> None:
    registry = RoleLoader().load_all(Path(".agent_teams/roles"))
    coordinator = registry.get("coordinator_agent")
    tools = set(coordinator.tools)

    assert "list_available_workflows" in tools
    assert "create_workflow_graph" in tools
    assert "dispatch_tasks" in tools
    assert "get_workflow_status" not in tools
    assert "materialize_code_shards_from_design" not in tools
    assert "manage_state" not in tools
    assert "query_task" not in tools
    assert "verify_task" not in tools
    assert "emit_event" not in tools
