# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

_KEYRING_SERVICE_NAME = "agent-teams.wechat"


class WeChatSecretStore:
    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        if not self.can_persist_token():
            return None
        assert keyring is not None
        try:
            return _normalize_secret(
                keyring.get_password(
                    _KEYRING_SERVICE_NAME,
                    self._account_name(config_dir, account_id),
                )
            )
        except Exception:
            return None

    def set_bot_token(self, config_dir: Path, account_id: str, token: str | None) -> None:
        normalized = _normalize_secret(token)
        if normalized is None:
            self.delete_bot_token(config_dir, account_id)
            return
        if not self.can_persist_token():
            raise RuntimeError(
                "WeChat token persistence requires a usable system keyring backend."
            )
        assert keyring is not None
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir, account_id),
                normalized,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist WeChat token to the system keyring."
            ) from exc

    def delete_bot_token(self, config_dir: Path, account_id: str) -> None:
        if not self.can_persist_token():
            return
        assert keyring is not None
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir, account_id),
            )
        except Exception:
            return

    def can_persist_token(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def _account_name(self, config_dir: Path, account_id: str) -> str:
        return f"{config_dir.expanduser().resolve()}::{account_id}"

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


_WECHAT_SECRET_STORE = WeChatSecretStore()


def get_wechat_secret_store() -> WeChatSecretStore:
    return _WECHAT_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
