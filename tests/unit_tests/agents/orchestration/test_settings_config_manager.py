# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.agents.orchestration.settings_config_manager import (
    OrchestrationSettingsConfigManager,
)


def test_get_orchestration_settings_rejects_legacy_main_agent_prompt(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestration.json").write_text(
        (
            "{\n"
            '  "main_agent_prompt": "legacy",\n'
            '  "default_orchestration_preset_id": "default",\n'
            '  "presets": [\n'
            "    {\n"
            '      "preset_id": "default",\n'
            '      "name": "Default",\n'
            '      "description": "General flow.",\n'
            '      "role_ids": ["Writer"],\n'
            '      "orchestration_prompt": "Delegate by capability."\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        _ = OrchestrationSettingsConfigManager(
            config_dir=config_dir
        ).get_orchestration_settings()
