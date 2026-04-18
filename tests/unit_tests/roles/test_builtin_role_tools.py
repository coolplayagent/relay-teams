# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.builtin import get_builtin_roles_dir
from relay_teams.roles import RoleMode
from relay_teams.roles.role_registry import RoleLoader


def test_builtin_roles_mount_expected_write_tools() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    coordinator = registry.get("Coordinator")
    crafter = registry.get("Crafter")
    daily_ai_report = registry.get("daily-ai-report")
    designer = registry.get("Designer")
    explorer = registry.get("Explorer")
    gater = registry.get("Gater")
    main_agent = registry.get("MainAgent")
    background_task_tools = {
        "shell",
        "list_background_tasks",
        "wait_background_task",
        "stop_background_task",
        "create_monitor",
        "list_monitors",
        "stop_monitor",
    }

    assert "write" in crafter.tools
    assert "edit" in crafter.tools
    assert "read" in crafter.tools
    assert "office_read_markdown" not in coordinator.tools
    assert "office_read_markdown" in crafter.tools
    assert "notebook_edit" in crafter.tools
    assert "notebook_edit" in main_agent.tools
    assert "office_read_markdown" in main_agent.tools
    assert "webfetch" in crafter.tools
    assert "websearch" in crafter.tools
    assert background_task_tools.issubset(set(crafter.tools))
    assert background_task_tools.issubset(set(gater.tools))
    assert background_task_tools.issubset(set(main_agent.tools))
    assert "spawn_subagent" in main_agent.tools
    assert background_task_tools.issubset(set(daily_ai_report.tools))
    assert main_agent.mode == RoleMode.PRIMARY
    assert crafter.mode == RoleMode.SUBAGENT
    assert designer.mode == RoleMode.SUBAGENT
    assert explorer.mode == RoleMode.SUBAGENT
    assert gater.mode == RoleMode.SUBAGENT
    assert daily_ai_report.mode == RoleMode.SUBAGENT
    assert "webfetch" in main_agent.tools
    assert "websearch" in main_agent.tools
    assert "skill-installer" in main_agent.skills
    assert "office_read_markdown" in daily_ai_report.tools
    assert "office_read_markdown" in designer.tools
    assert "write_tmp" in designer.tools
    assert "write" not in designer.tools
    assert "edit" not in designer.tools
    assert "office_read_markdown" in explorer.tools
    assert "write_tmp" in explorer.tools
    assert "write" not in explorer.tools
    assert "edit" not in explorer.tools
    assert "office_read_markdown" in gater.tools
    assert "write_tmp" in gater.tools
    assert "write" not in gater.tools
    assert "edit" not in gater.tools
