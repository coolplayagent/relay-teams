# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.env.web_config_models import (
    WebConfig,
    WebFallbackProvider,
    WebProvider,
)
from agent_teams.env.web_config_service import WebConfigService
from agent_teams.env.web_secret_store import WebSecretStore


class _FakeWebSecretStore(WebSecretStore):
    def __init__(self, *, can_persist: bool = True) -> None:
        self._api_keys: dict[str, str] = {}
        self._can_persist = can_persist

    def get_api_key(self, config_dir: Path) -> str | None:
        return self._api_keys.get(str(config_dir.resolve()))

    def set_api_key(self, config_dir: Path, api_key: str | None) -> None:
        if not self._can_persist:
            raise RuntimeError(
                "Web API key persistence requires a usable system keyring backend."
            )
        normalized_key = str(config_dir.resolve())
        if api_key is None:
            self._api_keys.pop(normalized_key, None)
            return
        self._api_keys[normalized_key] = api_key

    def delete_api_key(self, config_dir: Path) -> None:
        self._api_keys.pop(str(config_dir.resolve()), None)

    def can_persist_api_key(self) -> bool:
        return self._can_persist


def test_get_web_config_defaults_to_exa_without_api_key(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = WebConfigService(
        config_dir=config_dir,
        secret_store=_FakeWebSecretStore(),
    )

    assert service.get_web_config() == WebConfig(
        provider=WebProvider.EXA,
        api_key=None,
        fallback_provider=None,
        searxng_instance_url=None,
    )


def test_save_web_config_persists_provider_and_keyring_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeWebSecretStore()
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = WebConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_web_config(
        WebConfig(
            provider=WebProvider.EXA,
            api_key="secret",
            fallback_provider=WebFallbackProvider.SEARXNG,
            searxng_instance_url="https://search.example.test/",
        )
    )

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "AGENT_TEAMS_WEB_PROVIDER=exa\n"
        "AGENT_TEAMS_WEB_FALLBACK_PROVIDER=searxng\n"
        "AGENT_TEAMS_WEB_SEARXNG_INSTANCE_URL=https://search.example.test/\n"
    )
    assert secret_store.get_api_key(config_dir) == "secret"


def test_save_web_config_removes_plaintext_api_key_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "AGENT_TEAMS_WEB_PROVIDER=exa\nAGENT_TEAMS_WEB_API_KEY=old\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )
    service = WebConfigService(
        config_dir=config_dir,
        secret_store=_FakeWebSecretStore(),
    )

    service.save_web_config(WebConfig(provider=WebProvider.EXA, api_key=None))

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "AGENT_TEAMS_WEB_PROVIDER=exa\n"
    )


def test_get_web_config_reads_fallback_provider_and_instance_url(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        (
            "AGENT_TEAMS_WEB_PROVIDER=exa\n"
            "AGENT_TEAMS_WEB_FALLBACK_PROVIDER=searxng\n"
            "AGENT_TEAMS_WEB_SEARXNG_INSTANCE_URL=https://search.example.test\n"
        ),
        encoding="utf-8",
    )
    service = WebConfigService(
        config_dir=config_dir,
        secret_store=_FakeWebSecretStore(),
    )

    assert service.get_web_config() == WebConfig(
        provider=WebProvider.EXA,
        api_key=None,
        fallback_provider=WebFallbackProvider.SEARXNG,
        searxng_instance_url="https://search.example.test/",
    )
