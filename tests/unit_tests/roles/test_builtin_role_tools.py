# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.roles.role_registry import RoleLoader


def test_builtin_roles_mount_expected_write_tools() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    crafter = registry.get("Crafter")
    designer = registry.get("Designer")
    explorer = registry.get("Explorer")
    gater = registry.get("Gater")
    main_agent = registry.get("MainAgent")

    assert "write" in crafter.tools
    assert "edit" in crafter.tools
    assert "webfetch" in crafter.tools
    assert "websearch" in crafter.tools
    assert "webfetch" in main_agent.tools
    assert "websearch" in main_agent.tools
    assert "skill-installer" in main_agent.skills
    assert "write_tmp" in designer.tools
    assert "write" not in designer.tools
    assert "write_tmp" in explorer.tools
    assert "write" not in explorer.tools
    assert "write_tmp" in gater.tools
    assert "write" not in gater.tools
    assert crafter.mcp_servers == ("chrome-devtools",)
    assert designer.mcp_servers == ("chrome-devtools",)
    assert explorer.mcp_servers == ("chrome-devtools",)
    assert gater.mcp_servers == ("chrome-devtools",)
    assert main_agent.mcp_servers == ("chrome-devtools",)
