# -*- coding: utf-8 -*-
from __future__ import annotations

from json import dumps, loads
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.interfaces.server.ui_language_models import UiLanguageSettings


class UiLanguageSettingsService:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir = config_dir

    def get_ui_language_settings(self) -> UiLanguageSettings:
        config_file = self._config_dir / "ui.json"
        if not config_file.exists():
            return UiLanguageSettings()
        try:
            payload = _load_json_object(config_file)
            return UiLanguageSettings.model_validate(payload)
        except Exception:
            return UiLanguageSettings()

    def save_ui_language_settings(
        self,
        settings: UiLanguageSettings,
    ) -> UiLanguageSettings:
        config_file = self._config_dir / "ui.json"
        _ = config_file.write_text(
            dumps(settings.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )
        return settings


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text(encoding="utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
