# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.env import runtime_env


def test_load_merged_env_vars_reads_app_env_file(tmp_path: Path) -> None:
    user_home = tmp_path / "home"
    app_env_dir = user_home / ".config" / "agent-teams"
    app_env_dir.mkdir(parents=True)
    (app_env_dir / ".env").write_text(
        "APP_ONLY=one\nSHARED_KEY=app\n", encoding="utf-8"
    )

    merged = runtime_env.load_merged_env_vars(
        user_home_dir=user_home,
        include_process_env=False,
    )

    assert merged["SHARED_KEY"] == "app"
    assert merged["APP_ONLY"] == "one"


def test_get_env_var_process_env_has_highest_priority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    user_home = tmp_path / "home"
    app_env_dir = user_home / ".config" / "agent-teams"
    app_env_dir.mkdir(parents=True)
    (app_env_dir / ".env").write_text("ENV_KEY=app\n", encoding="utf-8")
    monkeypatch.setenv("ENV_KEY", "process")

    value = runtime_env.get_env_var(
        "ENV_KEY",
        user_home_dir=user_home,
    )

    assert value == "process"


def test_load_env_file_ignores_invalid_lines_and_strips_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nINVALID_LINE\nA=1\nB='two'\nC=\"three\"\n",
        encoding="utf-8",
    )

    values = runtime_env.load_env_file(env_file)

    assert values == {"A": "1", "B": "two", "C": "three"}


def test_get_env_var_returns_default_when_missing(tmp_path: Path) -> None:
    user_home = tmp_path / "home"

    value = runtime_env.get_env_var(
        "MISSING_KEY",
        default="fallback",
        user_home_dir=user_home,
        include_process_env=False,
    )

    assert value == "fallback"


def test_get_app_env_file_path_uses_app_config_dir(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path.resolve() / ".config" / "agent-teams"
    monkeypatch.setattr(runtime_env, "get_app_config_dir", lambda **kwargs: config_dir)

    env_file_path = runtime_env.get_app_env_file_path()

    assert env_file_path == config_dir / ".env"


def test_sync_app_env_to_process_env_applies_and_removes_managed_keys(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".config" / "agent-teams" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("SYNCED_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_PROCESS_ENV_BASELINE", {})
    monkeypatch.setattr(runtime_env, "_SYNCED_APP_ENV_KEYS", set())
    monkeypatch.delenv("SYNCED_KEY", raising=False)

    synced_env = runtime_env.sync_app_env_to_process_env(env_file)

    assert synced_env == {"SYNCED_KEY": "from-file"}
    assert runtime_env.os.environ["SYNCED_KEY"] == "from-file"

    env_file.write_text("", encoding="utf-8")
    runtime_env.sync_app_env_to_process_env(env_file)

    assert "SYNCED_KEY" not in runtime_env.os.environ


def test_sync_app_env_to_process_env_restores_baseline_values(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".config" / "agent-teams" / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("RESTORE_KEY=overlay\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_PROCESS_ENV_BASELINE", {"RESTORE_KEY": "base"})
    monkeypatch.setattr(runtime_env, "_SYNCED_APP_ENV_KEYS", set())
    monkeypatch.setenv("RESTORE_KEY", "base")

    runtime_env.sync_app_env_to_process_env(env_file)

    assert runtime_env.os.environ["RESTORE_KEY"] == "overlay"

    env_file.write_text("", encoding="utf-8")
    runtime_env.sync_app_env_to_process_env(env_file)

    assert runtime_env.os.environ["RESTORE_KEY"] == "base"
