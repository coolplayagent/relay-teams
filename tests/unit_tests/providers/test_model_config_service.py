# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from relay_teams.providers.model_config import (
    ModelConfigPayload,
    default_model_fallback_config,
)
from relay_teams.providers.model_catalog import ModelCatalogResult, ModelCatalogService
from relay_teams.providers.model_config_manager import ModelConfigManager
from relay_teams.providers.model_connectivity import (
    CodeAgentAuthVerifyResult,
    ModelConnectivityProbeService,
)
from relay_teams.providers.model_config_service import ModelConfigService
from relay_teams.providers.model_fallback_config_manager import (
    ModelFallbackConfigManager,
)
from relay_teams.sessions.runs.runtime_config import RuntimeConfig


class _RecordingModelConfigManager:
    def __init__(self) -> None:
        self.saved_config: dict[str, object] | None = None

    def get_model_config(self) -> dict[str, object]:
        return {}

    def get_model_profiles(self) -> dict[str, dict[str, object]]:
        return {}

    def save_model_profile(
        self,
        name: str,
        profile: dict[str, object],
        *,
        source_name: str | None = None,
    ) -> None:
        raise NotImplementedError

    def delete_model_profile(self, name: str) -> None:
        raise NotImplementedError

    def save_model_config(self, config: dict[str, object]) -> None:
        self.saved_config = config


class _RecordingModelFallbackConfigManager:
    def get_model_fallback_config(self) -> object:
        return default_model_fallback_config()

    def save_model_fallback_config(self, config: object) -> None:
        _ = config
        return None


class _RecordingModelCatalogService:
    def get_catalog(self, *, refresh: bool = False) -> ModelCatalogResult:
        return ModelCatalogResult(
            ok=True,
            source_url="https://models.dev/api.json",
            providers=(),
            stale=refresh,
        )


def test_save_model_config_preserves_omitted_optional_fields() -> None:
    manager = _RecordingModelConfigManager()
    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, manager),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )

    service.save_model_config(
        ModelConfigPayload.model_validate(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4.1",
                    "base_url": "https://example.test/v1",
                    "temperature": 0.2,
                    "top_p": 1.0,
                },
                "kimi": {
                    "provider": "openai_compatible",
                    "model": "kimi-k2.5",
                    "base_url": "https://example.test/v1",
                    "temperature": 0.2,
                    "top_p": 1.0,
                    "max_tokens": None,
                },
            }
        )
    )

    assert manager.saved_config == {
        "default": {
            "provider": "openai_compatible",
            "model": "gpt-4.1",
            "base_url": "https://example.test/v1",
            "temperature": 0.2,
            "top_p": 1.0,
        },
        "kimi": {
            "provider": "openai_compatible",
            "model": "kimi-k2.5",
            "base_url": "https://example.test/v1",
            "temperature": 0.2,
            "top_p": 1.0,
            "max_tokens": None,
        },
    }


def test_verify_codeagent_auth_delegates_to_probe_service() -> None:
    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, _RecordingModelConfigManager()),
        model_fallback_config_manager=cast(
            ModelFallbackConfigManager,
            _RecordingModelFallbackConfigManager(),
        ),
        model_catalog_service=cast(
            ModelCatalogService, _RecordingModelCatalogService()
        ),
        get_runtime=lambda: RuntimeConfig.model_construct(),
        on_runtime_reloaded=lambda runtime: None,
    )
    expected = CodeAgentAuthVerifyResult(
        status="valid",
        checked_at=datetime(2026, 4, 27, 2, 0, tzinfo=UTC),
        detail=None,
    )
    captured: dict[str, object] = {}

    class _FakeProbeService:
        def verify_codeagent_auth(
            self, *, profile_name: str
        ) -> CodeAgentAuthVerifyResult:
            captured["profile_name"] = profile_name
            return expected

    service._model_connectivity_probe_service = cast(
        ModelConnectivityProbeService, _FakeProbeService()
    )

    result = service.verify_codeagent_auth(profile_name="default")

    assert captured["profile_name"] == "default"
    assert result == expected
