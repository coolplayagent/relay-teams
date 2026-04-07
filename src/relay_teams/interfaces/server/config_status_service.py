# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue
from typing import cast

from relay_teams.mcp.mcp_models import McpConfigScope
from relay_teams.mcp.mcp_registry import McpRegistry
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
    ) -> None:
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime
        self._get_mcp_registry: Callable[[], McpRegistry] = get_mcp_registry
        self._get_skill_registry: Callable[[], SkillRegistry] = get_skill_registry
        self._get_proxy_status: Callable[[], dict[str, JsonValue]] = get_proxy_status

    def get_config_status(self) -> dict[str, JsonValue]:
        runtime = self._get_runtime()
        mcp_registry = self._get_mcp_registry()
        skill_registry = self._get_skill_registry()
        app_mcp_server_names = [
            spec.name
            for spec in mcp_registry.list_specs()
            if spec.source == McpConfigScope.APP
        ]
        skill_summaries = [
            skill.model_dump(mode="json")
            for skill in skill_registry.list_skill_summaries()
        ]
        status: dict[str, JsonValue] = {
            "model": cast(
                JsonValue,
                {
                    "loaded": runtime.model_status.loaded,
                    "profiles": list(runtime.model_status.profiles),
                    "error": runtime.model_status.error,
                },
            ),
            "mcp": cast(
                JsonValue,
                {
                    "loaded": True,
                    "servers": app_mcp_server_names,
                },
            ),
            "skills": cast(
                JsonValue,
                {
                    "loaded": True,
                    "skills": skill_summaries,
                },
            ),
            "proxy": self._get_proxy_status(),
        }
        return status
