# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

_KEYRING_SERVICE_NAME = "agent-teams.proxy-password"


class ProxySecretStore:
    def get_password(self, config_dir: Path) -> str | None:
        if not self.can_persist_password():
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

    def set_password(self, config_dir: Path, password: str | None) -> None:
        normalized_password = _normalize_secret(password)
        if normalized_password is None:
            self.delete_password(config_dir)
            return

        if not self.can_persist_password():
            raise RuntimeError(
                "Proxy password persistence requires a usable system keyring backend. "
                "Install/configure keyring, or keep the password only in .env/manual runtime input."
            )

        assert keyring is not None
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
                normalized_password,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist proxy password to the system keyring. "
                "Install/configure keyring, or keep the password only in .env/manual runtime input."
            ) from exc

    def delete_password(self, config_dir: Path) -> None:
        if not self.can_persist_password():
            return
        assert keyring is not None
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
            )
        except Exception:
            return

    def can_persist_password(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def backend_name(self) -> str:
        backend = self._get_backend()
        if backend is None:
            return "unavailable"
        return backend.__class__.__name__

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


_PROXY_SECRET_STORE = ProxySecretStore()


def get_proxy_secret_store() -> ProxySecretStore:
    return _PROXY_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value
