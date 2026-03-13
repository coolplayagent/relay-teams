# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from typing import cast

from agent_teams.notifications.config_manager import NotificationConfigManager
from agent_teams.notifications.models import NotificationConfig


class NotificationSettingsService:
    def __init__(
        self,
        *,
        notification_config_manager: NotificationConfigManager,
    ) -> None:
        self._notification_config_manager: NotificationConfigManager = (
            notification_config_manager
        )

    def get_notification_config(self) -> dict[str, JsonValue]:
        config = self._notification_config_manager.get_notification_config()
        return cast(dict[str, JsonValue], config.model_dump(mode="json"))

    def save_notification_config(self, config: dict[str, JsonValue]) -> None:
        validated = NotificationConfig.model_validate(config)
        self._notification_config_manager.save_notification_config(validated)
