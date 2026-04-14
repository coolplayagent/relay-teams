# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.env.github_config_models import (
    GitHubConfig,
    GitHubConfigUpdate,
    GitHubConfigView,
)
from relay_teams.env.github_config_service import GitHubConfigService
from relay_teams.env.github_secret_store import GitHubSecretStore


class _FakeGitHubSecretStore(GitHubSecretStore):
    def __init__(self, *, can_persist: bool = True) -> None:
        self._tokens: dict[str, str] = {}
        self._can_persist = can_persist

    def get_token(self, config_dir: Path) -> str | None:
        return self._tokens.get(str(config_dir.resolve()))

    def set_token(self, config_dir: Path, token: str | None) -> None:
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

    assert service.get_github_config() == GitHubConfig(
        token=None,
        webhook_base_url=None,
    )
    assert service.get_github_config_view() == GitHubConfigView(
        token_configured=False,
        webhook_base_url=None,
    )


def test_save_github_config_persists_keyring_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeGitHubSecretStore()
    monkeypatch.setattr(
        "relay_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_github_config(GitHubConfig(token="ghp_secret", webhook_base_url=None))

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
        "relay_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    service.save_github_config(GitHubConfig(token=None, webhook_base_url=None))

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
        "relay_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_github_config(
        GitHubConfig(token=" ghp_secret ", webhook_base_url=None)
    )

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "HTTP_PROXY=http://proxy.example:8080\n"
    )
    assert service.get_github_config() == GitHubConfig(
        token="ghp_secret",
        webhook_base_url=None,
    )


def test_save_github_config_persists_public_webhook_base_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "relay_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    service.save_github_config(
        GitHubConfig(
            token=None,
            webhook_base_url="https://agent-teams.example.com/automation/",
        )
    )

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "AGENT_TEAMS_GITHUB_WEBHOOK_BASE_URL=https://agent-teams.example.com/automation\n"
    )
    assert service.get_github_config() == GitHubConfig(
        token=None,
        webhook_base_url="https://agent-teams.example.com/automation",
    )


def test_save_github_config_rejects_local_webhook_base_url(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    with pytest.raises(ValueError, match="publicly reachable"):
        service.save_github_config(
            GitHubConfig(
                token=None,
                webhook_base_url="http://127.0.0.1:8000",
            )
        )


def test_get_github_config_drops_invalid_saved_webhook_base_url(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "AGENT_TEAMS_GITHUB_WEBHOOK_BASE_URL=http://127.0.0.1:8000\n",
        encoding="utf-8",
    )
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeGitHubSecretStore(),
    )

    assert service.get_github_config() == GitHubConfig(
        token=None,
        webhook_base_url=None,
    )
    assert (config_dir / ".env").read_text(encoding="utf-8") == ""


def test_update_github_config_keeps_existing_token_when_request_omits_it(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeGitHubSecretStore()
    secret_store.set_token(config_dir, "ghp_existing")
    service = GitHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    updated = service.update_github_config(
        GitHubConfigUpdate(
            webhook_base_url="https://agent-teams.example.com",
        )
    )

    assert updated == GitHubConfig(
        token="ghp_existing",
        webhook_base_url="https://agent-teams.example.com",
    )
    assert service.get_github_config_view() == GitHubConfigView(
        token_configured=True,
        webhook_base_url="https://agent-teams.example.com",
    )
