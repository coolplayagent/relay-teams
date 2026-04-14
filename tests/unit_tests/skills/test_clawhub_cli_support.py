# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import cast

from relay_teams.env.clawhub_cli import ClawHubCliInstallResult
from relay_teams.skills.clawhub_cli_support import run_clawhub_install


def test_run_clawhub_install_reports_runtime_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.os.environ",
        {
            "LANG": "zh_CN.UTF-8",
            "PATH": "/usr/bin",
            "CLAWHUB_TOKEN": "ch_secret",
        },
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = cast(list[str], args[0])
        env = kwargs.get("env")
        assert command == [
            "/usr/bin/clawhub",
            "--workdir",
            str(config_dir.resolve()),
            "--no-input",
            "install",
            "skill-creator-2",
            "--version",
            "v1.2.3",
            "--force",
        ]
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_secret"
        assert env["CLAWHUB_REGISTRY"] == "https://mirror-cn.clawhub.com"
        skill_dir = config_dir / "skills" / "skill-creator-2"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: skill-creator\n"
            "description: Create skills.\n"
            "---\n"
            "Use skill creator.\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="installed",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_clawhub_install(
        slug="skill-creator-2",
        version="v1.2.3",
        force=True,
        config_dir=config_dir,
    )

    assert result["ok"] is True
    assert result["slug"] == "skill-creator-2"
    assert result["requested_version"] == "v1.2.3"
    installed_skill = result.get("installed_skill")
    assert isinstance(installed_skill, dict)
    assert installed_skill["skill_id"] == "skill-creator-2"
    assert installed_skill["runtime_name"] == "skill-creator"
    assert installed_skill["ref"] == "app:skill-creator"
    diagnostics = result.get("diagnostics")
    assert isinstance(diagnostics, dict)
    assert diagnostics["registry"] == "https://mirror-cn.clawhub.com"
    assert diagnostics["skills_reloaded"] is False


def test_run_clawhub_install_installs_missing_binary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    installed_path = Path("/opt/tools/clawhub/bin/clawhub")
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.install_clawhub_via_npm",
        lambda *, timeout_seconds, base_env=None: ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(installed_path),
            npm_path="/usr/bin/npm",
            registry="https://mirrors.huaweicloud.com/repository/npm/",
        ),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.os.environ",
        {"PATH": "/usr/bin"},
    )

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = cast(list[str], args[0])
        env = kwargs.get("env")
        assert command == [
            str(installed_path),
            "--workdir",
            str(config_dir.resolve()),
            "--no-input",
            "install",
            "skill-creator",
        ]
        assert isinstance(env, dict)
        assert env["PATH"].split(os.pathsep)[0] == str(installed_path.parent)
        skill_dir = config_dir / "skills" / "skill-creator"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: skill-creator\ndescription: Create skills.\n---\nUse skill creator.\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="installed",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_clawhub_install(
        slug="skill-creator",
        config_dir=config_dir,
    )

    assert result["ok"] is True
    assert result["clawhub_path"] == str(installed_path)
    diagnostics = result.get("diagnostics")
    assert isinstance(diagnostics, dict)
    assert diagnostics["installation_attempted"] is True
    assert diagnostics["installed_during_install"] is True


def test_run_clawhub_install_reports_runtime_discovery_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.os.environ",
        {"PATH": "/usr/bin"},
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="installed",
            stderr="",
        ),
    )

    result = run_clawhub_install(
        slug="missing-runtime-skill",
        config_dir=config_dir,
    )

    assert result["ok"] is False
    assert result["error_code"] == "runtime_skill_unavailable"


def test_run_clawhub_install_rejects_unsupported_slug(tmp_path: Path) -> None:
    config_dir = tmp_path / ".relay-teams"

    result = run_clawhub_install(
        slug="org/skill-creator",
        config_dir=config_dir,
    )

    assert result["ok"] is False
    assert result["error_code"] == "unsupported_slug"


def test_run_clawhub_install_retries_without_endpoint_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_cli_support.os.environ",
        {"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )
    observed_envs: list[dict[str, str]] = []

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = cast(list[str], args[0])
        env = kwargs.get("env")
        assert isinstance(env, dict)
        observed_envs.append(dict(env))
        if len(observed_envs) == 1:
            assert env["CLAWHUB_REGISTRY"] == "https://mirror-cn.clawhub.com"
            return subprocess.CompletedProcess(
                args=command,
                returncode=1,
                stdout="",
                stderr="- Installing\nValidation error\nuser: invalid value",
            )
        assert "CLAWHUB_REGISTRY" not in env
        assert "CLAWHUB_SITE" not in env
        skill_dir = config_dir / "skills" / "skill-creator"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: skill-creator\ndescription: Create skills.\n---\nUse skill creator.\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="installed",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_clawhub_install(
        slug="skill-creator",
        config_dir=config_dir,
    )

    assert result["ok"] is True
    installed_skill = result.get("installed_skill")
    assert isinstance(installed_skill, dict)
    assert installed_skill["skill_id"] == "skill-creator"
    diagnostics = result.get("diagnostics")
    assert isinstance(diagnostics, dict)
    assert diagnostics["registry"] == "https://mirror-cn.clawhub.com"
    assert diagnostics["endpoint_fallback_used"] is True
