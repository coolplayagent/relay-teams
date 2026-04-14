from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from relay_teams.hooks import HookConfigLoader, HookConfigService


def test_get_hook_config_returns_summary_for_existing_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo ok"}],
                        }
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "shell",
                            "hooks": [
                                {
                                    "type": "http",
                                    "url": "http://127.0.0.1/hooks",
                                }
                            ],
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    service = HookConfigService(loader=HookConfigLoader(config_dir=config_dir))

    result = service.get_hook_config()

    assert result.exists is True
    assert result.summary.event_count == 2
    assert result.summary.matcher_group_count == 2
    assert result.summary.handler_count == 2
    config = cast(dict[str, JsonValue], result.config)
    hooks = cast(dict[str, JsonValue], config["hooks"])
    session_start_groups = cast(list[JsonValue], hooks["SessionStart"])
    first_group = cast(dict[str, JsonValue], session_start_groups[0])
    assert first_group["matcher"] == "*"


def test_validate_hook_config_returns_error_for_invalid_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "hooks.json").write_text(
        json.dumps({"hooks": {"SessionStart": {"matcher": "*"}}}),
        encoding="utf-8",
    )
    service = HookConfigService(loader=HookConfigLoader(config_dir=config_dir))

    result = service.validate_hook_config()

    assert result.valid is False
    assert result.exists is True
    assert result.error is not None
    assert "must be a list" in result.error
