# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.providers.model_config import (
    ModelFallbackConfig,
    default_model_fallback_config,
)


class ModelFallbackConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir: Path = config_dir

    def get_model_fallback_config(self) -> ModelFallbackConfig:
        fallback_file = self._config_dir / "model-fallback.json"
        if not fallback_file.exists():
            return default_model_fallback_config()
        try:
            payload = _load_json_object(fallback_file)
            return ModelFallbackConfig.model_validate(payload)
        except Exception:
            return default_model_fallback_config()

    def save_model_fallback_config(self, config: ModelFallbackConfig) -> None:
        fallback_file = self._config_dir / "model-fallback.json"
        _ = fallback_file.write_text(
            dumps(config.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text("utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
