# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from json import dumps, loads
from pathlib import Path
from typing import cast

from agent_teams.notifications.models import (
    NotificationConfig,
    default_notification_config,
)


class NotificationConfigManager:
    def __init__(self, *, config_dir: Path) -> None:
        self._config_dir: Path = config_dir

    def get_notification_config(self) -> NotificationConfig:
        notification_file = self._config_dir / "notifications.json"
        if not notification_file.exists():
            return default_notification_config()
        try:
            payload = _load_json_object(notification_file)
            return NotificationConfig.model_validate(payload)
        except Exception:
            return default_notification_config()

    def save_notification_config(self, config: NotificationConfig) -> None:
        notification_file = self._config_dir / "notifications.json"
        _ = notification_file.write_text(
            dumps(config.model_dump(mode="json"), indent=2),
            encoding="utf-8",
        )


def _load_json_object(file_path: Path) -> dict[str, JsonValue]:
    raw = cast(object, loads(file_path.read_text("utf-8")))
    if isinstance(raw, dict):
        return cast(dict[str, JsonValue], raw)
    return {}
