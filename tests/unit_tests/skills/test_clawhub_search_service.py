# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_cli import ClawHubCliInstallResult
from relay_teams.skills.clawhub_models import ClawHubSkillSearchRequest
from relay_teams.skills.clawhub_search_service import (
    ClawHubSkillSearchService,
    search_clawhub_skills,
)


def test_search_clawhub_skills_parses_search_output(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.os.environ",
        {"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_REGISTRY"] == "https://mirror-cn.clawhub.com"
        assert env["PATH"].split(os.pathsep)[0] == "/usr/bin"
        return subprocess.CompletedProcess(
            args=["clawhub", "search", "skill creator", "--limit", "2"],
            returncode=0,
            stdout=(
                "- Searching\n"
                "skill-creator  Skill Creator  (3.389)\n"
                "ai-skill-creator-attach-info v1.0.0  Skill Creator Attach Info  (66.021)\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = search_clawhub_skills(
        query="skill creator",
        limit=2,
        config_dir=tmp_path / ".relay-teams",
    )

    assert result.ok is True
    assert result.query == "skill creator"
    assert result.diagnostics.registry == "https://mirror-cn.clawhub.com"
    assert result.items[0].slug == "skill-creator"
    assert result.items[0].title == "Skill Creator"
    assert result.items[0].version is None
    assert result.items[0].score == 3.389
    assert result.items[1].slug == "ai-skill-creator-attach-info"
    assert result.items[1].title == "Skill Creator Attach Info"
    assert result.items[1].version == "v1.0.0"
    assert result.items[1].score == 66.021


def test_search_clawhub_skills_installs_missing_binary(
    monkeypatch,
    tmp_path: Path,
) -> None:
    installed_path = Path("/opt/tools/clawhub/bin/clawhub")
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.resolve_existing_clawhub_path",
        lambda: None,
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.install_clawhub_via_npm",
        lambda *, timeout_seconds, base_env=None: ClawHubCliInstallResult(
            ok=True,
            attempted=True,
            clawhub_path=str(installed_path),
            npm_path="/usr/bin/npm",
            registry="https://mirrors.huaweicloud.com/repository/npm/",
        ),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.os.environ",
        {"PATH": "/usr/bin"},
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["PATH"].split(os.pathsep)[0] == str(installed_path.parent)
        return subprocess.CompletedProcess(
            args=[str(installed_path), "search", "skill creator", "--limit", "1"],
            returncode=0,
            stdout="skill-creator  Skill Creator  (3.389)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = search_clawhub_skills(
        query="skill creator",
        limit=1,
        config_dir=tmp_path / ".relay-teams",
    )

    assert result.ok is True
    assert result.clawhub_path == str(installed_path)
    assert result.diagnostics.installation_attempted is True
    assert result.diagnostics.installed_during_search is True


def test_clawhub_search_service_reads_token_from_saved_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.os.environ",
        {"PATH": "/usr/bin"},
    )

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        assert env["CLAWHUB_TOKEN"] == "ch_saved"
        return subprocess.CompletedProcess(
            args=["clawhub", "search", "skill creator", "--limit", "1"],
            returncode=0,
            stdout="skill-creator  Skill Creator  (3.389)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    service = ClawHubSkillSearchService(
        config_dir=tmp_path / ".relay-teams",
        get_clawhub_config=lambda: ClawHubConfig(token="ch_saved"),
    )

    result = service.search(ClawHubSkillSearchRequest(query="skill creator", limit=1))

    assert result.ok is True
    assert result.diagnostics.token_configured is True


def test_search_clawhub_skills_retries_without_endpoint_overrides(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.resolve_existing_clawhub_path",
        lambda: Path("/usr/bin/clawhub"),
    )
    monkeypatch.setattr(
        "relay_teams.skills.clawhub_search_service.os.environ",
        {"LANG": "zh_CN.UTF-8", "PATH": "/usr/bin"},
    )
    observed_envs: list[dict[str, str]] = []

    def fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        observed_envs.append(dict(env))
        if len(observed_envs) == 1:
            assert env["CLAWHUB_REGISTRY"] == "https://mirror-cn.clawhub.com"
            return subprocess.CompletedProcess(
                args=["clawhub", "search", "skill creator", "--limit", "1"],
                returncode=1,
                stdout="",
                stderr="- Searching\nValidation error\nuser: invalid value",
            )
        assert "CLAWHUB_REGISTRY" not in env
        assert "CLAWHUB_SITE" not in env
        return subprocess.CompletedProcess(
            args=["clawhub", "search", "skill creator", "--limit", "1"],
            returncode=0,
            stdout="skill-creator  Skill Creator  (3.389)\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = search_clawhub_skills(
        query="skill creator",
        limit=1,
        config_dir=tmp_path / ".relay-teams",
    )

    assert result.ok is True
    assert result.items[0].slug == "skill-creator"
    assert result.diagnostics.registry == "https://mirror-cn.clawhub.com"
    assert result.diagnostics.endpoint_fallback_used is True
