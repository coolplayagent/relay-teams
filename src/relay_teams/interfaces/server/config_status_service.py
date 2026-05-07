# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue

from relay_teams.mcp.mcp_models import McpConfigScope
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.plugins import PluginRegistry
from relay_teams.plugins.views import build_public_plugin_registry
from relay_teams.sessions.runs.runtime_config import RuntimeConfig
from relay_teams.skills.skill_registry import SkillRegistry


class ConfigStatusService:
    def __init__(
        self,
        *,
        get_runtime: Callable[[], RuntimeConfig],
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_proxy_status: Callable[[], dict[str, JsonValue]],
        get_plugin_registry: Callable[[], PluginRegistry] | None = None,
    ) -> None:
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime
        self._get_mcp_registry: Callable[[], McpRegistry] = get_mcp_registry
        self._get_skill_registry: Callable[[], SkillRegistry] = get_skill_registry
        self._get_proxy_status: Callable[[], dict[str, JsonValue]] = get_proxy_status
        self._get_plugin_registry = get_plugin_registry

    def get_config_status(self) -> dict[str, JsonValue]:
        runtime = self._get_runtime()
        mcp_registry = self._get_mcp_registry()
        skill_registry = self._get_skill_registry()
        app_mcp_server_names: list[JsonValue] = [
            spec.name
            for spec in mcp_registry.list_specs()
            if spec.source == McpConfigScope.APP
        ]
        skill_summaries: list[JsonValue] = [
            skill.model_dump(mode="json")
            for skill in skill_registry.list_skill_summaries()
        ]
        model_profiles: list[JsonValue] = list(runtime.model_status.profiles)
        model_status: JsonValue = {
            "loaded": runtime.model_status.loaded,
            "profiles": model_profiles,
            "error": runtime.model_status.error,
        }
        mcp_status: JsonValue = {
            "loaded": True,
            "servers": app_mcp_server_names,
        }
        skills_status: JsonValue = {
            "loaded": True,
            "skills": skill_summaries,
        }
        status: dict[str, JsonValue] = {
            "model": model_status,
            "mcp": mcp_status,
            "skills": skills_status,
            "proxy": self._get_proxy_status(),
        }
        if self._get_plugin_registry is not None:
            plugin_registry = self._get_plugin_registry()
            public_plugin_registry = build_public_plugin_registry(plugin_registry)
            plugin_summaries: list[JsonValue] = [
                {
                    "name": plugin.name,
                    "version": plugin.version,
                    "scope": plugin.scope.value,
                    "enabled": plugin.enabled,
                    "root_dir": str(plugin.root_dir),
                    "skill_count": len(plugin.skill_sources),
                    "role_count": len(plugin.role_sources),
                    "command_count": len(plugin.command_sources),
                    "hook_count": len(plugin.hook_sources),
                    "mcp_server_config_count": len(plugin.mcp_sources),
                    "monitor_count": len(plugin.monitor_sources),
                }
                for plugin in plugin_registry.plugins
            ]
            plugin_diagnostics: list[JsonValue] = [
                diagnostic.model_dump(mode="json")
                for diagnostic in public_plugin_registry.diagnostics
            ]
            plugins_status: JsonValue = {
                "loaded": True,
                "plugins": plugin_summaries,
                "diagnostics": plugin_diagnostics,
            }
            status["plugins"] = plugins_status
        return status
