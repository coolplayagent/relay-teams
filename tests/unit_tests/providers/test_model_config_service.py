# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

from relay_teams.providers.model_config import ModelConfigPayload
from relay_teams.providers.model_config_manager import ModelConfigManager
from relay_teams.providers.model_config_service import ModelConfigService
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


def test_save_model_config_preserves_omitted_optional_fields() -> None:
    manager = _RecordingModelConfigManager()
    service = ModelConfigService(
        config_dir=Path("."),
        roles_dir=Path("."),
        db_path=Path("relay-teams.db"),
        model_config_manager=cast(ModelConfigManager, manager),
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
