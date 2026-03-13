# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.builtin import get_builtin_roles_dir
from agent_teams.roles.models import RoleDefinition
from agent_teams.roles.registry import RoleLoader, RoleRegistry


def test_role_loader_loads_markdown_role() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())
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


def test_role_registry_resolves_dynamic_coordinator_role() -> None:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="Coordinator",
            name="Coordinator",
            version="1.0.0",
            tools=(
                "list_available_roles",
                "create_tasks",
                "update_task",
                "list_run_tasks",
                "dispatch_task",
            ),
            system_prompt="Coordinate tasks.",
        )
    )
    registry.register(
        RoleDefinition(
            role_id="Crafter",
            name="Crafter",
            version="1.0.0",
            tools=("read",),
            system_prompt="Implement tasks.",
        )
    )

    assert registry.get_coordinator_role_id() == "Coordinator"
    assert registry.is_coordinator_role("Coordinator") is True
    assert registry.is_coordinator_role("Crafter") is False
