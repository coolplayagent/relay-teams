# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.logger import get_logger
from relay_teams.plugins.plugin_models import PluginSettingsSource
from relay_teams.roles.role_registry import RoleRegistry

LOGGER = get_logger(__name__)


def resolve_plugin_default_agent_role_id(
    *,
    settings_sources: tuple[PluginSettingsSource, ...],
    role_registry: RoleRegistry,
) -> str | None:
    for source in settings_sources:
        raw_agent = str(source.settings.agent or "").strip()
        if not raw_agent:
            continue
        try:
            return role_registry.resolve_normal_mode_role_id(raw_agent)
        except ValueError:
            LOGGER.warning(
                "Ignoring invalid plugin default agent setting",
                extra={
                    "plugin_name": source.plugin_name,
                    "settings_path": str(source.path),
                    "agent": raw_agent,
                },
            )
    return None
