# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.env.clawhub_auth import get_clawhub_runtime_home
from relay_teams.env.clawhub_config_models import ClawHubConfig
from relay_teams.env.clawhub_config_service import ClawHubConfigService
from relay_teams.env.clawhub_secret_store import ClawHubSecretStore


class _FakeClawHubSecretStore(ClawHubSecretStore):
    def __init__(self, *, fail_on_get_token: bool = False) -> None:
        self._tokens: dict[str, str] = {}
        self._fail_on_get_token = fail_on_get_token

    def get_token(self, config_dir: Path) -> str | None:
        if self._fail_on_get_token:
            raise AssertionError("token value should not be read")
        return self._tokens.get(str(config_dir.resolve()))

    def has_token_reference(self, config_dir: Path) -> bool:
        return str(config_dir.resolve()) in self._tokens

    def set_token(self, config_dir: Path, token: str | None) -> None:
        normalized_key = str(config_dir.resolve())
        if token is None:
            self._tokens.pop(normalized_key, None)
            return
        self._tokens[normalized_key] = token

    def delete_token(self, config_dir: Path) -> None:
        self._tokens.pop(str(config_dir.resolve()), None)

    def can_persist_token(self) -> bool:
        return True


def test_get_clawhub_config_defaults_to_empty_token(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeClawHubSecretStore(),
    )

    assert service.get_clawhub_config() == ClawHubConfig(token=None)


def test_has_configured_clawhub_token_reference_uses_env_without_secret_read(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text("CLAWHUB_TOKEN=ch_env\n", encoding="utf-8")
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeClawHubSecretStore(fail_on_get_token=True),
    )

    assert service.has_configured_token_reference() is True


def test_has_configured_clawhub_token_reference_uses_saved_reference_only(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeClawHubSecretStore(fail_on_get_token=True)
    secret_store.set_token(config_dir, "ch_secret")
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    assert service.has_configured_token_reference() is True


def test_save_clawhub_config_persists_keyring_secret(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeClawHubSecretStore()
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=secret_store,
    )

    service.save_clawhub_config(ClawHubConfig(token="ch_secret"))

    assert (config_dir / ".env").read_text(encoding="utf-8") == ""
    assert secret_store.get_token(config_dir) == "ch_secret"


def test_save_clawhub_config_removes_plaintext_env_token(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "CLAWHUB_TOKEN=legacy\nHTTP_PROXY=http://proxy.example:8080\n",
        encoding="utf-8",
    )
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeClawHubSecretStore(),
    )

    service.save_clawhub_config(ClawHubConfig(token=None))

    assert (config_dir / ".env").read_text(encoding="utf-8") == (
        "HTTP_PROXY=http://proxy.example:8080\n"
    )


def test_save_clawhub_config_clears_runtime_home_when_token_removed(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / ".relay-teams"
    config_dir.mkdir(parents=True)
    runtime_home = get_clawhub_runtime_home(config_dir)
    runtime_home.mkdir(parents=True, exist_ok=True)
    (runtime_home / "marker.txt").write_text("present", encoding="utf-8")
    service = ClawHubConfigService(
        config_dir=config_dir,
        secret_store=_FakeClawHubSecretStore(),
    )

    service.save_clawhub_config(ClawHubConfig(token=None))

    assert not runtime_home.exists()
