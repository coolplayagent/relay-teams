# -*- coding: utf-8 -*-
from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from pathlib import Path

from pydantic import ValidationError

from relay_teams.general.models import GeneralConfig, GeneralConfigUpdate


class GeneralConfigService:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_file = config_dir / "general.json"

    def get_config(self) -> GeneralConfig:
        if not self._config_file.exists():
            return GeneralConfig()
        try:
            payload = loads(self._config_file.read_text(encoding="utf-8"))
        except (JSONDecodeError, OSError, UnicodeError):
            return GeneralConfig()
        if not isinstance(payload, dict):
            return GeneralConfig()
        try:
            return GeneralConfig.model_validate(payload)
        except ValidationError:
            return GeneralConfig()

    def save_config(self, config: GeneralConfigUpdate) -> GeneralConfig:
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        payload = config.model_dump(mode="json", exclude_none=True)
        self._config_file.write_text(
            dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return self.get_config()
