# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from collections.abc import Callable

from agent_teams.mcp.registry import McpRegistry
from agent_teams.sessions.runs.runtime_config import RuntimeConfig

from agent_teams.skills.registry import SkillRegistry


class ConfigStatusService:
    def __init__(
        self,
        *,
        get_runtime: Callable[[], RuntimeConfig],
        get_mcp_registry: Callable[[], McpRegistry],
        get_skill_registry: Callable[[], SkillRegistry],
        get_proxy_status: Callable[[], dict[str, JsonValue]],
    ) -> None:
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime
        self._get_mcp_registry: Callable[[], McpRegistry] = get_mcp_registry
        self._get_skill_registry: Callable[[], SkillRegistry] = get_skill_registry
        self._get_proxy_status: Callable[[], dict[str, JsonValue]] = get_proxy_status

    def get_config_status(self) -> dict[str, JsonValue]:
        runtime = self._get_runtime()
        mcp_registry = self._get_mcp_registry()
        skill_registry = self._get_skill_registry()
        return {
            "model": {
                "loaded": runtime.model_status.loaded,
                "profiles": list(runtime.model_status.profiles),
                "error": runtime.model_status.error,
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
