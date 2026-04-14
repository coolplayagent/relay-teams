# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

from relay_teams.env.clawhub_auth import (
    build_clawhub_managed_subprocess_env,
    clear_clawhub_runtime_home,
    ensure_clawhub_cli_login,
    get_clawhub_runtime_config_path,
    get_clawhub_runtime_home,
)


def test_build_clawhub_managed_subprocess_env_sets_isolated_home(monkeypatch) -> None:
    config_dir = Path("/tmp/.relay-teams")
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.os.environ",
        {"PATH": "/usr/bin"},
    )

    env = build_clawhub_managed_subprocess_env(
        "ch_secret",
        config_dir=config_dir,
        base_env={"PATH": "/usr/bin"},
    )

    runtime_home = get_clawhub_runtime_home(config_dir)
    assert env["CLAWHUB_TOKEN"] == "ch_secret"
    assert env["HOME"] == str(runtime_home)
    assert env["USERPROFILE"] == str(runtime_home)
    assert env["XDG_CONFIG_HOME"] == str(runtime_home / ".config")


def test_ensure_clawhub_cli_login_uses_token_login_and_returns_runtime_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.os.environ",
        {"PATH": "/usr/bin"},
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        assert command == [
            "/usr/bin/clawhub",
            "login",
            "--token",
            "ch_secret",
            "--no-browser",
        ]
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        assert env["HOME"] == str(get_clawhub_runtime_home(config_dir))
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="- Verifying token\n✔ OK. Logged in as @steven",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ensure_clawhub_cli_login(
        "ch_secret",
        config_dir=config_dir,
        base_env={"PATH": "/usr/bin"},
    )

    assert result.ok is True
    assert result.endpoint_fallback_used is False
    assert result.registry is None
    assert result.env["HOME"] == str(get_clawhub_runtime_home(config_dir))


def test_ensure_clawhub_cli_login_retries_without_endpoint_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.os.environ",
        {"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )
    observed_envs: list[dict[str, str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        assert isinstance(command, list)
        env = kwargs.get("env")
        assert isinstance(env, dict)
        observed_envs.append(dict(env))
        if len(observed_envs) == 1:
            assert env["CLAWHUB_REGISTRY"] == "https://mirror-cn.clawhub.com"
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr="- Verifying token\nValidation error\nuser: invalid value",
            )
        assert "CLAWHUB_REGISTRY" not in env
        assert "CLAWHUB_SITE" not in env
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="- Verifying token\n✔ OK. Logged in as @steven",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ensure_clawhub_cli_login(
        "ch_secret",
        config_dir=config_dir,
        base_env={"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )

    assert result.ok is True
    assert result.registry == "https://mirror-cn.clawhub.com"
    assert result.endpoint_fallback_used is True


def test_ensure_clawhub_cli_login_reuses_existing_runtime_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    runtime_config = get_clawhub_runtime_config_path(config_dir)
    runtime_config.parent.mkdir(parents=True, exist_ok=True)
    runtime_config.write_text(
        '{"token": "ch_secret"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_auth.os.environ",
        {"PATH": "/usr/bin"},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("login command should not run")
        ),
    )

    result = ensure_clawhub_cli_login(
        "ch_secret",
        config_dir=config_dir,
        base_env={"PATH": "/usr/bin"},
    )

    assert result.ok is True
    assert result.env["HOME"] == str(get_clawhub_runtime_home(config_dir))


def test_clear_clawhub_runtime_home_removes_login_state(tmp_path: Path) -> None:
    config_dir = tmp_path / ".relay-teams"
    runtime_config = get_clawhub_runtime_config_path(config_dir)
    runtime_config.parent.mkdir(parents=True, exist_ok=True)
    runtime_config.write_text('{"token":"ch_secret"}', encoding="utf-8")

    clear_clawhub_runtime_home(config_dir)

    assert not get_clawhub_runtime_home(config_dir).exists()
