# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from relay_teams.logger import get_logger
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.skills.skill_registry import SkillRegistry
from relay_teams.trace import trace_span

LOGGER = get_logger(__name__)


class SkillsConfigReloadService:
    def __init__(
        self,
        *,
        config_dir: Path,
        role_registry: RoleRegistry,
        on_skill_reloaded: Callable[[SkillRegistry], None],
    ) -> None:
        self._config_dir: Path = config_dir
        self._role_registry: RoleRegistry = role_registry
        self._on_skill_reloaded: Callable[[SkillRegistry], None] = on_skill_reloaded

    def reload_skills_config(self) -> SkillRegistry:
        with trace_span(
            LOGGER,
            component="skills.config",
            operation="reload",
            attributes={"config_dir": str(self._config_dir)},
        ):
            skill_registry = SkillRegistry.from_config_dirs(
                app_config_dir=self._config_dir
            )
            for role in self._role_registry.list_roles():
                skill_registry.resolve_known(
                    role.skills,
                    strict=False,
                    consumer=f"skills.config_reload.role:{role.role_id}",
                )
            self._on_skill_reloaded(skill_registry)
            return skill_registry
