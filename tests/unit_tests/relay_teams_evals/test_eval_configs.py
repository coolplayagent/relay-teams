from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from relay_teams.paths.root_paths import get_app_config_dir


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _mapping(value: object) -> Mapping[object, object]:
    assert isinstance(value, Mapping)
    return value


def test_bundled_swebench_configs_use_current_app_config_dir() -> None:
    expected_config_dir = f"~/{get_app_config_dir(Path('~')).name}"
    configs_dir = _repo_root() / ".agent_teams" / "evals" / "configs"
    config_paths = sorted(configs_dir.glob("**/eval-swebench*.yaml"))

    assert config_paths
    for config_path in config_paths:
        raw_config: object = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config = _mapping(raw_config)
        agent_teams = _mapping(config["agent_teams"])

        assert agent_teams["config_dir"] == expected_config_dir
