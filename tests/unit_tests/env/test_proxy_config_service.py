# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from agent_teams.env.proxy_config_service import ProxyConfigService
from agent_teams.env.proxy_env import (
    ProxyEnvConfig,
    ProxyEnvInput,
    sync_proxy_env_to_process_env,
)
from agent_teams.env.proxy_secret_store import ProxySecretStore


class _FakeProxySecretStore(ProxySecretStore):
    def __init__(self, *, can_persist: bool = True) -> None:
        self._passwords: dict[str, str] = {}
        self._can_persist = can_persist

    def get_password(self, config_dir: Path) -> str | None:
        return self._passwords.get(str(config_dir.resolve()))

    def set_password(self, config_dir: Path, password: str | None) -> None:
        normalized_key = str(config_dir.resolve())
        if password is None:
            self._passwords.pop(normalized_key, None)
            return
        self._passwords[normalized_key] = password

    def delete_password(self, config_dir: Path) -> None:
        self._passwords.pop(str(config_dir.resolve()), None)

    def can_persist_password(self) -> bool:
        return self._can_persist


def _clear_proxy_env(monkeypatch) -> None:
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
        "SSL_VERIFY",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_test_app_config_dir(monkeypatch, config_dir: Path) -> None:
    monkeypatch.setattr(
        "agent_teams.env.runtime_env.get_app_config_dir",
        lambda user_home_dir=None: config_dir,
    )


def test_get_proxy_status_masks_embedded_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)
    (config_dir / ".env").write_text(
        "HTTP_PROXY=http://user:pass@proxy.example:8080\n",
        encoding="utf-8",
    )
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=_FakeProxySecretStore(),
    )

    status = service.get_proxy_status()

    assert status["has_proxy"] is True
    assert status["http_proxy"] == "http://***:***@proxy.example:8080"


def test_reload_proxy_config_passes_current_config_to_callback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)
    (config_dir / ".env").write_text(
        "NO_PROXY=localhost,127.0.0.1\n",
        encoding="utf-8",
    )
    captured: list[ProxyEnvConfig] = []
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=captured.append,
        secret_store=_FakeProxySecretStore(),
    )

    service.reload_proxy_config()

    assert captured == [
        ProxyEnvConfig(
            http_proxy=None,
            https_proxy=None,
            all_proxy=None,
            no_proxy="localhost,127.0.0.1",
            ssl_verify=None,
        )
    ]


def test_get_saved_proxy_config_reads_app_env_values(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)
    (config_dir / ".env").write_text(
        "HTTP_PROXY=http://proxy.example:8080\nNO_PROXY=localhost,127.0.0.1\n",
        encoding="utf-8",
    )
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=_FakeProxySecretStore(),
    )

    saved_config = service.get_saved_proxy_config()

    assert saved_config == ProxyEnvInput(
        http_proxy="http://proxy.example:8080",
        https_proxy=None,
        all_proxy=None,
        no_proxy="localhost,127.0.0.1",
        proxy_username=None,
        proxy_password=None,
    )


def test_save_proxy_config_rewrites_managed_keys_and_reloads_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)
    (config_dir / ".env").write_text(
        "# existing\nFOO=bar\nhttp_proxy=http://old.example:8080\nNO_PROXY=localhost\n",
        encoding="utf-8",
    )
    reloaded_configs: list[ProxyEnvConfig] = []
    secret_store = _FakeProxySecretStore()
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=reloaded_configs.append,
        secret_store=secret_store,
    )

    service.save_proxy_config(
        ProxyEnvInput(
            https_proxy="http://proxy.example:8443",
            no_proxy="localhost,127.0.0.1",
            proxy_username="alice",
            proxy_password="secret",
        )
    )

    saved_text = (config_dir / ".env").read_text(encoding="utf-8")
    assert saved_text == (
        "# existing\n"
        "FOO=bar\n"
        "NO_PROXY=localhost,127.0.0.1\n"
        "HTTPS_PROXY=http://alice@proxy.example:8443\n"
    )
    assert secret_store.get_password(config_dir) == "secret"
    assert reloaded_configs == [
        ProxyEnvConfig(
            http_proxy=None,
            https_proxy="http://alice:secret@proxy.example:8443",
            all_proxy=None,
            no_proxy="localhost,127.0.0.1",
            ssl_verify=None,
        )
    ]


def test_get_saved_proxy_config_extracts_shared_proxy_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        (
            "HTTP_PROXY=http://alice:secret@proxy.example:8080\n"
            "HTTPS_PROXY=http://alice:secret@secure.example:8443\n"
        ),
        encoding="utf-8",
    )
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=_FakeProxySecretStore(),
    )

    saved_config = service.get_saved_proxy_config()

    assert saved_config == ProxyEnvInput(
        http_proxy="http://proxy.example:8080",
        https_proxy="http://secure.example:8443",
        all_proxy=None,
        no_proxy=None,
        proxy_username="alice",
        proxy_password="secret",
    )


def test_get_saved_proxy_config_reads_password_from_secret_store(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(
        "HTTPS_PROXY=http://alice@secure.example:8443\n",
        encoding="utf-8",
    )
    secret_store = _FakeProxySecretStore()
    secret_store.set_password(config_dir, "secret")
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=secret_store,
    )

    saved_config = service.get_saved_proxy_config()

    assert saved_config == ProxyEnvInput(
        http_proxy=None,
        https_proxy="http://secure.example:8443",
        all_proxy=None,
        no_proxy=None,
        proxy_username="alice",
        proxy_password="secret",
    )


def test_save_proxy_config_falls_back_to_secret_file_without_keyring(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    secret_store = _FakeProxySecretStore(can_persist=False)
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=secret_store,
    )

    service.save_proxy_config(
        ProxyEnvInput(
            https_proxy="http://proxy.example:8443",
            proxy_username="alice",
            proxy_password="secret",
        )
    )

    assert secret_store.get_password(config_dir) == "secret"


def test_save_proxy_config_rejects_multiple_distinct_proxy_passwords(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=lambda _config: None,
        secret_store=_FakeProxySecretStore(),
    )

    try:
        service.save_proxy_config(
            ProxyEnvInput(
                http_proxy="http://alice:first@proxy.example:8080",
                https_proxy="http://bob:second@proxy.example:8443",
            )
        )
    except ValueError as exc:
        assert "multiple distinct proxy passwords" in str(exc)
    else:
        raise AssertionError(
            "Expected save to fail for distinct embedded proxy passwords."
        )


def test_save_proxy_config_clears_runtime_proxy_env_when_proxy_removed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)

    def _reload_proxy_env(proxy_config: ProxyEnvConfig) -> None:
        sync_proxy_env_to_process_env(proxy_config)

    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=_reload_proxy_env,
        secret_store=_FakeProxySecretStore(),
    )

    service.save_proxy_config(ProxyEnvInput(http_proxy="http://bad-proxy.invalid:8080"))

    assert os.environ["HTTP_PROXY"] == "http://bad-proxy.invalid:8080"
    assert os.environ["http_proxy"] == "http://bad-proxy.invalid:8080"

    service.save_proxy_config(ProxyEnvInput())

    assert "HTTP_PROXY" not in os.environ
    assert "http_proxy" not in os.environ
    assert service.get_proxy_config() == ProxyEnvConfig(
        http_proxy=None,
        https_proxy=None,
        all_proxy=None,
        no_proxy=None,
        ssl_verify=None,
    )


def test_reload_proxy_config_uses_effective_process_proxy_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _clear_proxy_env(monkeypatch)
    config_dir = tmp_path / ".agent-teams"
    config_dir.mkdir(parents=True)
    _set_test_app_config_dir(monkeypatch, config_dir)
    (config_dir / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("HTTP_PROXY", "http://bad-proxy.invalid:8080")
    monkeypatch.setenv("http_proxy", "http://bad-proxy.invalid:8080")
    captured: list[ProxyEnvConfig] = []
    service = ProxyConfigService(
        config_dir=config_dir,
        on_proxy_reloaded=captured.append,
        secret_store=_FakeProxySecretStore(),
    )

    service.reload_proxy_config()

    assert captured == [
        ProxyEnvConfig(
            http_proxy="http://bad-proxy.invalid:8080",
            https_proxy=None,
            all_proxy=None,
            no_proxy=None,
            ssl_verify=None,
        )
    ]
