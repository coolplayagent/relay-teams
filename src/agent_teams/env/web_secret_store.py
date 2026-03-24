# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

_KEYRING_SERVICE_NAME = "agent-teams.web-api-key"


class WebSecretStore:
    def get_api_key(self, config_dir: Path) -> str | None:
        if not self.can_persist_api_key():
            return None
        assert keyring is not None
        try:
            return _normalize_secret(
                keyring.get_password(
                    _KEYRING_SERVICE_NAME,
                    self._account_name(config_dir),
                )
            )
        except Exception:
            return None

    def set_api_key(self, config_dir: Path, api_key: str | None) -> None:
        normalized_api_key = _normalize_secret(api_key)
        if normalized_api_key is None:
            self.delete_api_key(config_dir)
            return

        if not self.can_persist_api_key():
            raise RuntimeError(
                "Web API key persistence requires a usable system keyring backend."
            )

        assert keyring is not None
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
                normalized_api_key,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist web API key to the system keyring."
            ) from exc

    def delete_api_key(self, config_dir: Path) -> None:
        if not self.can_persist_api_key():
            return
        assert keyring is not None
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
            )
        except Exception:
            return

    def can_persist_api_key(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def _account_name(self, config_dir: Path) -> str:
        return str(config_dir.expanduser().resolve())

    def _get_backend(self) -> object | None:
        if keyring is None:
            return None
        try:
            backend = keyring.get_keyring()
        except Exception:
            return None
        if backend is None:
            return None
        return backend


_WEB_SECRET_STORE = WebSecretStore()


def get_web_secret_store() -> WebSecretStore:
    return _WEB_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value
