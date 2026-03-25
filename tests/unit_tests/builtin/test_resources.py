# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

from agent_teams.builtin.resources import ensure_app_config_bootstrap


def test_ensure_app_config_bootstrap_seeds_empty_model_config(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".config" / "agent-teams"

    ensure_app_config_bootstrap(config_dir)

    model_config_path = config_dir / "model.json"
    assert model_config_path.exists()
    assert json.loads(model_config_path.read_text(encoding="utf-8")) == {}
