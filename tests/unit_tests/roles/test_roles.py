# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.roles.registry import RoleLoader


def test_role_loader_loads_markdown_role() -> None:
    registry = RoleLoader().load_all(Path(".agent_teams/roles"))
    roles = registry.list_roles()
    assert len(roles) >= 1
    assert roles[0].role_id


def test_role_loader_rejects_depends_on_in_role_front_matter(tmp_path: Path) -> None:
    role_file = tmp_path / "bad_role.md"
    role_file.write_text(
        "---\n"
        "role_id: bad_role\n"
        "name: Bad Role\n"
        "version: 1.0.0\n"
        "tools: []\n"
        "depends_on: []\n"
        "---\n"
        "System prompt.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="depends_on is not allowed"):
        RoleLoader().load_one(role_file)
