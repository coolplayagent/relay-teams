# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from collections.abc import Callable
from pathlib import Path

from agent_teams.providers.model_config import ProviderModelInfo, ProviderType
from agent_teams.providers.model_connectivity import (
    ModelDiscoveryRequest,
    ModelDiscoveryResult,
    ModelConnectivityProbeRequest,
    ModelConnectivityProbeResult,
    ModelConnectivityProbeService,
)
from agent_teams.providers.model_config_manager import ModelConfigManager
from agent_teams.providers.provider_registry import list_provider_models
from agent_teams.sessions.runs.runtime_config import RuntimeConfig, load_runtime_config


class ModelConfigService:
    def __init__(
        self,
        *,
        config_dir: Path,
        roles_dir: Path,
        db_path: Path,
        model_config_manager: ModelConfigManager,
        get_runtime: Callable[[], RuntimeConfig],
        on_runtime_reloaded: Callable[[RuntimeConfig], None],
    ) -> None:
        self._config_dir: Path = config_dir
        self._roles_dir: Path = roles_dir
        self._db_path: Path = db_path
        self._model_config_manager: ModelConfigManager = model_config_manager
        self._get_runtime: Callable[[], RuntimeConfig] = get_runtime
        self._on_runtime_reloaded: Callable[[RuntimeConfig], None] = on_runtime_reloaded
        self._model_connectivity_probe_service = ModelConnectivityProbeService(
            get_runtime=get_runtime
        )

    @property
    def runtime(self) -> RuntimeConfig:
        return self._get_runtime()

    def get_model_config(self) -> dict[str, JsonValue]:
        return self._model_config_manager.get_model_config()

    def get_model_profiles(self) -> dict[str, dict[str, JsonValue]]:
        return self._model_config_manager.get_model_profiles()

    def get_provider_models(
        self,
        *,
        provider: ProviderType | None = None,
    ) -> tuple[ProviderModelInfo, ...]:
        return list_provider_models(self.runtime.llm_profiles, provider)

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, JsonValue],
        *,
        source_name: str | None = None,
    ) -> None:
        self._model_config_manager.save_model_profile(
            name,
            profile,
            source_name=source_name,
        )
        self.reload_model_config()

    def delete_model_profile(self, name: str) -> None:
        self._model_config_manager.delete_model_profile(name)
        self.reload_model_config()

    def save_model_config(self, config: dict[str, JsonValue]) -> None:
        self._model_config_manager.save_model_config(config)
        self.reload_model_config()

    def probe_connectivity(
        self,
        request: ModelConnectivityProbeRequest,
    ) -> ModelConnectivityProbeResult:
        return self._model_connectivity_probe_service.probe(request)

    def discover_models(
        self,
        request: ModelDiscoveryRequest,
    ) -> ModelDiscoveryResult:
        return self._model_connectivity_probe_service.discover_models(request)

    def reload_model_config(self) -> None:
        runtime = load_runtime_config(
            config_dir=self._config_dir,
            roles_dir=self._roles_dir,
            db_path=self._db_path,
        )
        self._on_runtime_reloaded(runtime)
