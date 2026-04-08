# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.agents.orchestration.settings_models import OrchestrationSettings


class OrchestrationSettingsConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir = config_dir

    def get_orchestration_settings(self) -> OrchestrationSettings:
        config_file = self._config_dir / "orchestration.json"
        if not config_file.exists():
            raise FileNotFoundError(f"Missing orchestration config: {config_file}")
        payload = _load_json_object(config_file)
        return OrchestrationSettings.model_validate(payload)

    def save_orchestration_settings(self, settings: OrchestrationSettings) -> None:
        config_file = self._config_dir / "orchestration.json"
        _ = config_file.write_text(
            dumps(settings.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
