# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.roles.role_registry import RoleLoader


def test_builtin_roles_mount_edit_where_write_is_available() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    crafter = registry.get("Crafter")
    designer = registry.get("Designer")

    assert "write" in crafter.tools
    assert "edit" in crafter.tools
    assert "write" in designer.tools
    assert "edit" in designer.tools
