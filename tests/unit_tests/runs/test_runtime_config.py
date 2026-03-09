# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_teams.runs import runtime_config


def test_load_runtime_config_uses_project_config_dir_by_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent_teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-model",
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "test-key",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_config, "get_project_config_dir", lambda: config_dir)
    monkeypatch.setattr(runtime_config, "load_merged_env_vars", lambda **kwargs: {})

    resolved = runtime_config.load_runtime_config()

    assert resolved.paths.config_dir == config_dir.resolve()
    assert resolved.paths.env_file == (config_dir / ".env").resolve()
    assert resolved.paths.roles_dir == (config_dir / "roles")
    assert resolved.paths.db_path == (config_dir / "agent_teams.db")


def test_load_runtime_config_resolves_relative_roles_dir_from_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent_teams"
    config_dir.mkdir(parents=True)
    (config_dir / "model.json").write_text(
        json.dumps(
            {
                "default": {
                    "model": "fake-model",
                    "base_url": "http://localhost:8000/v1",
                    "api_key": "test-key",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runtime_config,
        "load_merged_env_vars",
        lambda **kwargs: {"AGENT_TEAMS_ROLES_DIR": "roles"},
    )

    resolved = runtime_config.load_runtime_config(config_dir=config_dir)

    assert resolved.paths.roles_dir == (config_dir / "roles")


def test_load_llm_configs_error_mentions_model_file_only(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as exc_info:
        runtime_config.load_llm_configs(tmp_path, {})

    assert "Please create model.json with a 'default' profile." in str(exc_info.value)


def test_load_llm_configs_reads_provider_field(tmp_path: Path) -> None:
    model_file = tmp_path / "model.json"
    model_file.write_text(
        json.dumps(
            {
                "default": {
                    "provider": "openai_compatible",
                    "model": "gpt-4o-mini",
                    "base_url": "https://example.test/v1",
                    "api_key": "plain-text-key",
                }
            }
        ),
        encoding="utf-8",
    )

    profiles = runtime_config.load_llm_configs(tmp_path, {})

    assert profiles["default"].provider.value == "openai_compatible"
