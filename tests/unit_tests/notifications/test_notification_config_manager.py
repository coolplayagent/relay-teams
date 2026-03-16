# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.notifications import (
    NotificationConfigManager,
    default_notification_config,
)
from agent_teams.notifications.models import NotificationConfig


def test_get_notification_config_returns_default_when_missing(tmp_path: Path) -> None:
    manager = NotificationConfigManager(config_dir=tmp_path)

    loaded = manager.get_notification_config()

    assert loaded.model_dump(mode="json") == default_notification_config().model_dump(
        mode="json"
    )


def test_get_notification_config_returns_default_when_invalid_json(
    tmp_path: Path,
) -> None:
    (tmp_path / "notifications.json").write_text("{invalid", encoding="utf-8")
    manager = NotificationConfigManager(config_dir=tmp_path)

    loaded = manager.get_notification_config()

    assert loaded.model_dump(mode="json") == default_notification_config().model_dump(
        mode="json"
    )


def test_save_notification_config_round_trip(tmp_path: Path) -> None:
    manager = NotificationConfigManager(config_dir=tmp_path)
    config = NotificationConfig.model_validate(
        {
            "tool_approval_requested": {"enabled": True, "channels": ["browser"]},
            "run_completed": {"enabled": True, "channels": ["toast"]},
            "run_failed": {"enabled": True, "channels": ["browser", "toast"]},
            "run_stopped": {"enabled": False, "channels": ["toast"]},
        }
    )

    manager.save_notification_config(config)
    loaded = manager.get_notification_config()

    assert loaded.model_dump(mode="json") == config.model_dump(mode="json")
