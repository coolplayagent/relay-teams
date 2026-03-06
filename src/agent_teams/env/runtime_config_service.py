# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast

from agent_teams.env.config_manager import ConfigManager
from agent_teams.env.runtime_config import RuntimeConfig, load_runtime_config
from agent_teams.mcp.registry import McpRegistry
from agent_teams.notifications import NotificationConfig
from agent_teams.providers.model_config import ProviderModelInfo, ProviderType
from agent_teams.providers.registry import list_provider_models
from agent_teams.roles.registry import RoleRegistry
from agent_teams.shared_types.json_types import JsonObject
from agent_teams.skills.registry import SkillRegistry


class RuntimeConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        roles_dir: Path,
        db_path: Path,
        runtime: RuntimeConfig,
        config_manager: ConfigManager,
        role_registry: RoleRegistry,
        mcp_registry: McpRegistry,
        skill_registry: SkillRegistry,
        on_runtime_reloaded: Callable[[RuntimeConfig], None],
        on_mcp_reloaded: Callable[[McpRegistry], None],
        on_skill_reloaded: Callable[[SkillRegistry], None],
    ) -> None:
        self._config_dir: Path = config_dir
        self._roles_dir: Path = roles_dir
        self._db_path: Path = db_path
        self._runtime: RuntimeConfig = runtime
        self._config_manager: ConfigManager = config_manager
        self._role_registry: RoleRegistry = role_registry
        self._mcp_registry: McpRegistry = mcp_registry
        self._skill_registry: SkillRegistry = skill_registry
        self._on_runtime_reloaded: Callable[[RuntimeConfig], None] = on_runtime_reloaded
        self._on_mcp_reloaded: Callable[[McpRegistry], None] = on_mcp_reloaded
        self._on_skill_reloaded: Callable[[SkillRegistry], None] = on_skill_reloaded

    @property
    def runtime(self) -> RuntimeConfig:
        return self._runtime

    def get_config_status(self) -> JsonObject:
        return {
            "model": {
                "loaded": True,
                "profiles": list(self._runtime.llm_profiles.keys()),
            },
            "mcp": {
                "loaded": True,
                "servers": list(self._mcp_registry.list_names()),
            },
            "skills": {
                "loaded": True,
                "skills": list(self._skill_registry.list_names()),
            },
        }

    def get_model_config(self) -> JsonObject:
        return self._config_manager.get_model_config()

    def get_model_profiles(self) -> dict[str, JsonObject]:
        return self._config_manager.get_model_profiles()

    def get_provider_models(
        self,
        *,
        provider: ProviderType | None = None,
    ) -> tuple[ProviderModelInfo, ...]:
        return list_provider_models(self._runtime.llm_profiles, provider)

    def save_model_profile(self, name: str, profile: JsonObject) -> None:
        self._config_manager.save_model_profile(name, profile)
        self.reload_model_config()

    def delete_model_profile(self, name: str) -> None:
        self._config_manager.delete_model_profile(name)
        self.reload_model_config()

    def save_model_config(self, config: JsonObject) -> None:
        self._config_manager.save_model_config(config)
        self.reload_model_config()

    def get_notification_config(self) -> JsonObject:
        config = self._config_manager.get_notification_config()
        return cast(JsonObject, config.model_dump(mode="json"))

    def save_notification_config(self, config: JsonObject) -> None:
        validated = NotificationConfig.model_validate(config)
        self._config_manager.save_notification_config(validated)

    def reload_model_config(self) -> None:
        runtime = load_runtime_config(
            config_dir=self._config_dir,
            roles_dir=self._roles_dir,
            db_path=self._db_path,
        )
        self._runtime = runtime
        self._on_runtime_reloaded(runtime)

    def reload_mcp_config(self) -> None:
        mcp_registry = self._config_manager.load_mcp_registry()
        for role in self._role_registry.list_roles():
            mcp_registry.validate_known(role.mcp_servers)
        self._mcp_registry = mcp_registry
        self._on_mcp_reloaded(mcp_registry)

    def reload_skills_config(self) -> None:
        skill_registry = SkillRegistry.from_config_dirs(
            project_config_dir=self._config_dir
        )
        for role in self._role_registry.list_roles():
            skill_registry.validate_known(role.skills)
        self._skill_registry = skill_registry
        self._on_skill_reloaded(skill_registry)
