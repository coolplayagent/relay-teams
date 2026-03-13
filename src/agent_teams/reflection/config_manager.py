# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.reflection.models import ReflectionConfig, default_reflection_config


class ReflectionConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir = config_dir

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    def get_reflection_config(self) -> ReflectionConfig:
        config_file = self._config_dir / "reflection.json"
        if not config_file.exists():
            return default_reflection_config()
        try:
            return ReflectionConfig.model_validate(_load_json_object(config_file))
        except Exception:
            return default_reflection_config()

    def save_reflection_config(self, config: ReflectionConfig) -> None:
        config_file = self._config_dir / "reflection.json"
        _ = config_file.write_text(
            dumps(config.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
