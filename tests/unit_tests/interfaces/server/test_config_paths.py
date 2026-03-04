from __future__ import annotations

from pathlib import Path

from agent_teams.interfaces.server import config_paths


def test_get_config_dir_uses_default_when_env_not_set(monkeypatch) -> None:
    monkeypatch.delenv(config_paths.CONFIG_DIR_ENV_VAR, raising=False)
    default_root = Path("D:/repo-root").resolve()
    monkeypatch.setattr(config_paths, "get_project_root", lambda: default_root)

    config_dir = config_paths.get_config_dir()

    assert config_dir == default_root / ".agent_teams"


def test_get_config_dir_prefers_env_override(monkeypatch) -> None:
    override_dir = Path("D:/tmp/custom-config").resolve()
    monkeypatch.setenv(config_paths.CONFIG_DIR_ENV_VAR, str(override_dir))

    config_dir = config_paths.get_config_dir()

    assert config_dir == override_dir
