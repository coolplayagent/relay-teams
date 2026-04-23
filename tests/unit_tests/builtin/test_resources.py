# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from relay_teams.builtin.resources import (
    ensure_app_config_bootstrap,
    get_builtin_roles_dir,
)
from relay_teams.roles.role_registry import RoleLoader


def test_ensure_app_config_bootstrap_seeds_empty_model_config(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"

    ensure_app_config_bootstrap(config_dir)

    model_config_path = config_dir / "model.json"
    assert model_config_path.exists()
    assert json.loads(model_config_path.read_text(encoding="utf-8")) == {}


def test_primary_builtin_runtime_roles_allow_all_mcp_servers_and_skills() -> None:
    registry = RoleLoader().load_all(get_builtin_roles_dir())

    for role_id in ("MainAgent", "Crafter"):
        role = registry.get(role_id)
        assert role.mcp_servers == ("*",)
        assert role.skills == ("*",)
