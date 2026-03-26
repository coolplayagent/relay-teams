# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.mcp import (
    McpConfigManager,
    McpConfigReloadService,
)
from agent_teams.roles.role_models import RoleDefinition
from agent_teams.roles.role_registry import RoleRegistry


def test_reload_mcp_config_ignores_unknown_servers_on_existing_roles(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir()
    role_registry = RoleRegistry()
    role_registry.register(
        RoleDefinition(
            role_id="writer",
            name="Writer",
            description="Writes documents.",
            version="1.0.0",
            tools=(),
            mcp_servers=("missing_server",),
            skills=(),
            model_profile="default",
            system_prompt="Write clearly.",
        )
    )
    reloaded_registries = []
    service = McpConfigReloadService(
        mcp_config_manager=McpConfigManager(app_config_dir=app_config_dir),
        role_registry=role_registry,
        on_mcp_reloaded=lambda registry: reloaded_registries.append(registry),
    )

    service.reload_mcp_config()

    assert len(reloaded_registries) == 1
    assert reloaded_registries[0].list_names() == ()
