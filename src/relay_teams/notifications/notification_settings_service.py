# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from typing import cast

from relay_teams.notifications.notification_config_manager import (
    NotificationConfigManager,
)
from relay_teams.notifications.models import NotificationConfig


class NotificationSettingsService:
    def __init__(
        self,
        *,
        notification_config_manager: NotificationConfigManager,
    ) -> None:
        self._notification_config_manager: NotificationConfigManager = (
            notification_config_manager
        )

    def get_notification_config(self) -> NotificationConfig:
        return self._notification_config_manager.get_notification_config()

    def get_notification_config_payload(self) -> dict[str, JsonValue]:
        config = self.get_notification_config()
        return cast(dict[str, JsonValue], config.model_dump(mode="json"))

    def save_notification_config(self, config: NotificationConfig) -> None:
        self._notification_config_manager.save_notification_config(config)
