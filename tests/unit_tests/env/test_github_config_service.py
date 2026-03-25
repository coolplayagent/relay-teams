# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.env.github_config_models import GitHubConfig
from agent_teams.env.github_config_service import GitHubConfigService
from agent_teams.env.github_secret_store import GitHubSecretStore


class _FakeGitHubSecretStore(GitHubSecretStore):
    def __init__(self, *, can_persist: bool = True) -> None:
        self._tokens: dict[str, str] = {}
        self._can_persist = can_persist

    def get_token(self, config_dir: Path) -> str | None:
        return self._tokens.get(str(config_dir.resolve()))

    def set_token(self, config_dir: Path, token: str | None) -> None:
        if not self._can_persist:
            raise RuntimeError(
                "GitHub token persistence requires a usable system keyring backend."
            )
        normalized_key = str(config_dir.resolve())
        if token is None:
            self._tokens.pop(normalized_key, None)
            return
        self._tokens[normalized_key] = token

    def delete_token(self, config_dir: Path) -> None:
        self._tokens.pop(str(config_dir.resolve()), None)

    def can_persist_token(self) -> bool:
        return self._can_persist


def test_get_github_config_defaults_to_empty_token(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    assert service.get_github_config() == GitHubConfig(token=None)


def test_save_github_config_persists_keyring_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeGitHubSecretStore()
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_github_config(GitHubConfig(token="ghp_secret"))

    assert (config_dir / ".env").read_text(encoding="utf-8") == ""
    assert secret_store.get_token(config_dir) == "ghp_secret"


def test_save_github_config_removes_plaintext_env_tokens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "GH_TOKEN=old\nGITHUB_TOKEN=older\nHTTP_PROXY=http://proxy.example:8080\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    service.save_github_config(GitHubConfig(token=None))

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "HTTP_PROXY=http://proxy.example:8080\n"
    )


def test_save_github_config_falls_back_to_env_when_keyring_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "GITHUB_TOKEN=legacy\nHTTP_PROXY=http://proxy.example:8080\n",
        encoding="utf-8",
    )
    secret_store = _FakeGitHubSecretStore(can_persist=False)
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_github_config(GitHubConfig(token=" ghp_secret "))

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "HTTP_PROXY=http://proxy.example:8080\nGH_TOKEN=ghp_secret\n"
    )
    assert service.get_github_config() == GitHubConfig(token="ghp_secret")
    assert secret_store.get_token(config_dir) is None
