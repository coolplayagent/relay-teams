# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from pydantic import JsonValue

from relay_teams.agents.orchestration.settings_config_manager import (
    OrchestrationSettingsConfigManager,
)
from relay_teams.agents.orchestration.settings_models import (
    OrchestrationSettings,
)
from relay_teams.roles.role_registry import (
    RoleRegistry,
    is_reserved_system_role_definition,
)
from relay_teams.sessions.runs.run_models import RunTopologySnapshot
from relay_teams.sessions.session_models import SessionMode, SessionRecord
from relay_teams.sessions.session_repository import SessionRepository


class OrchestrationSettingsService:
    def __init__(
        self,
        *,
        config_manager: OrchestrationSettingsConfigManager,
        session_repo: SessionRepository,
        get_role_registry: Callable[[], RoleRegistry],
    ) -> None:
        self._config_manager = config_manager
        self._session_repo = session_repo
        self._get_role_registry = get_role_registry

    def get_orchestration_config(self) -> OrchestrationSettings:
        return self._config_manager.get_orchestration_settings()

    def get_orchestration_config_payload(self) -> dict[str, JsonValue]:
        settings = self.get_orchestration_config()
        return cast(dict[str, JsonValue], settings.model_dump(mode="json"))

    def save_orchestration_config(self, config: OrchestrationSettings) -> None:
        self._validate_roles(config)
        self._config_manager.save_orchestration_settings(config)
        self._session_repo.reconcile_orchestration_presets(
            valid_preset_ids=tuple(preset.preset_id for preset in config.presets),
            default_preset_id=config.default_orchestration_preset_id or None,
        )

    def default_session_mode(self) -> SessionMode:
        return SessionMode.NORMAL

    def default_orchestration_preset_id(self) -> str | None:
        settings = self._config_manager.get_orchestration_settings()
        return settings.default_orchestration_preset_id or None

    def resolve_run_topology(self, session: SessionRecord) -> RunTopologySnapshot:
        settings = self._config_manager.get_orchestration_settings()
        role_registry = self._get_role_registry()
        main_agent_role_id = role_registry.get_main_agent_role_id()
        normal_root_role_id = role_registry.resolve_normal_mode_role_id(
            session.normal_root_role_id
        )
        coordinator_role_id = role_registry.get_coordinator_role_id()
        if session.session_mode == SessionMode.NORMAL:
            return RunTopologySnapshot(
                session_mode=SessionMode.NORMAL,
                main_agent_role_id=main_agent_role_id,
                normal_root_role_id=normal_root_role_id,
                coordinator_role_id=coordinator_role_id,
                orchestration_preset_id=session.orchestration_preset_id,
            )

        preset_id = (
            session.orchestration_preset_id or settings.default_orchestration_preset_id
        )
        if not preset_id:
            raise ValueError("No orchestration preset configured")
        preset = next(
            (
                candidate
                for candidate in settings.presets
                if candidate.preset_id == preset_id
            ),
            None,
        )
        if preset is None:
            raise ValueError(f"Unknown orchestration preset: {preset_id}")
        return RunTopologySnapshot(
            session_mode=SessionMode.ORCHESTRATION,
            main_agent_role_id=main_agent_role_id,
            normal_root_role_id=normal_root_role_id,
            coordinator_role_id=coordinator_role_id,
            orchestration_preset_id=preset.preset_id,
            orchestration_prompt=preset.orchestration_prompt,
            allowed_role_ids=preset.role_ids,
        )

    def _validate_roles(self, settings: OrchestrationSettings) -> None:
        registry = self._get_role_registry()
        for preset in settings.presets:
            for role_id in preset.role_ids:
                role = registry.get(role_id)
                if is_reserved_system_role_definition(role):
                    raise ValueError(
                        f"Reserved system role cannot be used in orchestration presets: {role_id}"
                    )
