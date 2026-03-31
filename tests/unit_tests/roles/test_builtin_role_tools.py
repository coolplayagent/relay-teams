# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.roles.role_registry import RoleLoader


def test_builtin_roles_mount_expected_write_tools() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    crafter = registry.get("Crafter")
    daily_ai_report = registry.get("daily-ai-report")
    designer = registry.get("Designer")
    explorer = registry.get("Explorer")
    gater = registry.get("Gater")
    main_agent = registry.get("MainAgent")
    exec_session_tools = {
        "exec_command",
        "list_exec_sessions",
        "write_stdin",
        "resize_exec_session",
        "terminate_exec_session",
    }

    assert "write" in crafter.tools
    assert "edit" in crafter.tools
    assert "webfetch" in crafter.tools
    assert "websearch" in crafter.tools
    assert exec_session_tools.issubset(set(crafter.tools))
    assert exec_session_tools.issubset(set(gater.tools))
    assert exec_session_tools.issubset(set(main_agent.tools))
    assert exec_session_tools.issubset(set(daily_ai_report.tools))
    assert "webfetch" in main_agent.tools
    assert "websearch" in main_agent.tools
    assert "skill-installer" in main_agent.skills
    assert "write" in designer.tools
    assert "edit" not in designer.tools
    assert "write" in explorer.tools
    assert "edit" not in explorer.tools
    assert "write" in gater.tools
    assert "edit" not in gater.tools
