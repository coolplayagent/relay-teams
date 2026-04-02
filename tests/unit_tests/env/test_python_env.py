# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import agent_teams.env.python_env as python_env_module

from agent_teams.env import (
    AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY,
    bind_subprocess_python_env,
)


def test_bind_subprocess_python_env_prefers_python_from_target_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    system_python = tmp_path / "system" / "python"
    system_python.parent.mkdir(parents=True)
    system_python.write_text("", encoding="utf-8")
    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        python_env_module.shutil,
        "which",
        lambda command, path=None: (
            str(system_python)
            if command == "python" and path == str(system_python.parent)
            else None
        ),
    )
    monkeypatch.setattr(python_env_module.sys, "executable", str(fallback_python))

    env = bind_subprocess_python_env({"PATH": str(system_python.parent)})

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(system_python.resolve())
    assert env["PATH"] == str(system_python.parent)


def test_bind_subprocess_python_env_falls_back_to_current_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fallback_python = tmp_path / "fallback" / "python"
    fallback_python.parent.mkdir(parents=True)
    fallback_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        python_env_module.shutil,
        "which",
        lambda command, path=None: None,
    )
    monkeypatch.setattr(python_env_module.sys, "executable", str(fallback_python))

    env = bind_subprocess_python_env({"PATH": str(tmp_path / "bin")})

    assert env[AGENT_TEAMS_PYTHON_EXECUTABLE_ENV_KEY] == str(fallback_python.resolve())
    assert env["PATH"].split(python_env_module.os.pathsep)[0] == str(
        fallback_python.parent
    )
