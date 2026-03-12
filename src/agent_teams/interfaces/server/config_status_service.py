# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from agent_teams.mcp.registry import McpRegistry
from agent_teams.runs.runtime_config import RuntimeConfig
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.skills.registry import SkillRegistry


class ConfigStatusService:
    def __init__(
        self,
        *,
        get_runtime: Callable[[], RuntimeConfig],
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_proxy_status: Callable[[], JsonObject],
    ) -> None:
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime
        self._get_mcp_registry: Callable[[], McpRegistry] = get_mcp_registry
        self._get_skill_registry: Callable[[], SkillRegistry] = get_skill_registry
        self._get_proxy_status: Callable[[], JsonObject] = get_proxy_status

    def get_config_status(self) -> JsonObject:
        runtime = self._get_runtime()
        mcp_registry = self._get_mcp_registry()
        skill_registry = self._get_skill_registry()
        return {
            "model": {
                "loaded": True,
                "profiles": list(runtime.llm_profiles.keys()),
            },
            "mcp": {
                "loaded": True,
                "servers": list(mcp_registry.list_names()),
            },
            "skills": {
                "loaded": True,
                "skills": list(skill_registry.list_names()),
            },
            "proxy": self._get_proxy_status(),
        }
