# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

import relay_teams.agents.execution.system_prompts as system_prompts
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_config_service import ClawHubConfigService
from relay_teams.env.github_config_models import GitHubConfig
from relay_teams.env.github_config_service import GitHubConfigService


def test_github_environment_status_checks_reference_without_revealing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_checks: list[str] = []

    def _has_reference(_self: GitHubConfigService) -> bool:
        reference_checks.append("checked")
        return True

    def _get_config(_self: GitHubConfigService) -> GitHubConfig:
        raise AssertionError("prompt status should not read the GitHub token")

    monkeypatch.setattr(
        "relay_teams.paths.get_app_config_dir",
        lambda user_home_dir=None: tmp_path,
    )
    monkeypatch.setattr(
        "relay_teams.net.github_cli.resolve_system_gh_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("prompt status should not scan for gh")
        ),
    )
    monkeypatch.setattr(
        GitHubConfigService,
        "has_configured_token_reference",
        _has_reference,
    )
    monkeypatch.setattr(GitHubConfigService, "get_github_config", _get_config)

    assert system_prompts._get_github_cli_environment_status() == (True, None)
    assert reference_checks == ["checked"]


def test_clawhub_environment_status_checks_reference_without_revealing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference_checks: list[str] = []

    def _has_reference(_self: ClawHubConfigService) -> bool:
        reference_checks.append("checked")
        return True

    def _get_config(_self: ClawHubConfigService) -> ClawHubConfig:
        raise AssertionError("prompt status should not read the ClawHub token")

    monkeypatch.setattr(
        "relay_teams.paths.get_app_config_dir",
        lambda user_home_dir=None: tmp_path,
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_cli.resolve_existing_clawhub_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("prompt status should not scan for clawhub")
        ),
    )
    monkeypatch.setattr(
        ClawHubConfigService,
        "has_configured_token_reference",
        _has_reference,
    )
    monkeypatch.setattr(ClawHubConfigService, "get_clawhub_config", _get_config)

    assert system_prompts._get_clawhub_environment_status() == (True, None)
    assert reference_checks == ["checked"]


def test_github_environment_status_avoids_resolving_config_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    config_dir.mkdir()
    (config_dir / ".env").write_text("GH_TOKEN=ghp_env\n", encoding="utf-8")

    def _raise_resolve(_self: Path, strict: bool = False) -> Path:
        _ = strict
        raise AssertionError("prompt status should not resolve config paths")

    monkeypatch.setattr(
        "relay_teams.paths.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    monkeypatch.setattr(
        "relay_teams.net.github_cli.resolve_system_gh_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("prompt status should not scan for gh")
        ),
    )
    monkeypatch.setattr(Path, "resolve", _raise_resolve)

    assert system_prompts._get_github_cli_environment_status() == (True, None)


def test_clawhub_environment_status_avoids_resolving_secret_index_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    config_dir.mkdir()
    (config_dir / "secrets.json").write_text(
        (
            "{\n"
            '  "version": 1,\n'
            '  "entries": [\n'
            "    {\n"
            '      "namespace": "clawhub_config",\n'
            '      "owner_id": "default",\n'
            '      "field_name": "token",\n'
            '      "storage": "keyring"\n'
            "    }\n"
            "  ]\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    def _raise_resolve(_self: Path, strict: bool = False) -> Path:
        _ = strict
        raise AssertionError("prompt status should not resolve config paths")

    monkeypatch.setattr(
        "relay_teams.paths.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    monkeypatch.setattr(
        "relay_teams.env.clawhub_cli.resolve_existing_clawhub_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("prompt status should not scan for clawhub")
        ),
    )
    monkeypatch.setattr(Path, "resolve", _raise_resolve)

    assert system_prompts._get_clawhub_environment_status() == (True, None)
