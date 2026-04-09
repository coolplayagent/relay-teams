# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import cast

from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_cli import ClawHubCliInstallResult
from relay_teams.skills.clawhub_install_service import (
    ClawHubSkillInstallService,
    install_clawhub_skill,
)
from relay_teams.skills.clawhub_models import ClawHubSkillInstallRequest


def test_install_clawhub_skill_reports_runtime_identity(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    reload_events: list[str] = []
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.os.environ",
        {"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
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

    result = install_clawhub_skill(
        slug="skill-creator-2",
        version="v1.2.3",
        force=True,
        token="ch_secret",
        config_dir=config_dir,
        on_skill_installed=lambda: reload_events.append("reloaded"),
    )

    assert result.ok is True
    assert result.slug == "skill-creator-2"
    assert result.requested_version == "v1.2.3"
    assert result.installed_skill is not None
    assert result.installed_skill.skill_id == "skill-creator-2"
    assert result.installed_skill.runtime_name == "skill-creator"
    assert result.installed_skill.ref == "app:skill-creator"
    assert result.diagnostics.registry == "https://mirror-cn.clawhub.com"
    assert result.diagnostics.skills_reloaded is True
    assert reload_events == ["reloaded"]


def test_install_clawhub_skill_installs_missing_binary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    installed_path = Path("/opt/tools/clawhub/bin/clawhub")
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.install_clawhub_via_npm",
        lambda *, timeout_seconds, base_env=None: ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(installed_path),
            npm_path="/usr/bin/npm",
            registry="https://mirrors.huaweicloud.com/repository/npm/",
        ),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.os.environ",
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

    result = install_clawhub_skill(
        slug="skill-creator",
        config_dir=config_dir,
    )

    assert result.ok is True
    assert result.clawhub_path == str(installed_path)
    assert result.diagnostics.installation_attempted is True
    assert result.diagnostics.installed_during_install is True


def test_install_clawhub_skill_reports_runtime_discovery_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.os.environ",
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

    result = install_clawhub_skill(
        slug="missing-runtime-skill",
        config_dir=config_dir,
    )

    assert result.ok is False
    assert result.error_code == "runtime_skill_unavailable"


def test_install_clawhub_skill_rejects_unsupported_slug(tmp_path: Path) -> None:
    config_dir = tmp_path / ".relay-teams"

    result = install_clawhub_skill(
        slug="org/skill-creator",
        config_dir=config_dir,
    )

    assert result.ok is False
    assert result.error_code == "unsupported_slug"


def test_clawhub_install_service_reads_token_from_saved_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_install_service.os.environ",
        {"PATH": "/usr/bin"},
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_saved"
        skill_dir = config_dir / "skills" / "skill-creator"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: skill-creator\ndescription: Create skills.\n---\nUse skill creator.\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=["clawhub", "install", "skill-creator"],
            returncode=0,
            stdout="installed",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubSkillInstallService(
        config_dir=config_dir,
        get_clawhub_config=lambda: ClawHubConfig(token="ch_saved"),
    )

    result = service.install(ClawHubSkillInstallRequest(slug="skill-creator"))

    assert result.ok is True
    assert result.diagnostics.token_configured is True
