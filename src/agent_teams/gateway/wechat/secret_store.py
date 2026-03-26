# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from agent_teams.secrets import AppSecretStore, get_secret_store

_LEGACY_KEYRING_SERVICE_NAME = "agent-teams.wechat"
_NAMESPACE = "wechat_account"
_FIELD_NAME = "bot_token"


class WeChatSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        migrated = self._migrate_legacy_keyring(config_dir, account_id)
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
        )
        return _normalize_secret(value if value is not None else migrated)

    def set_bot_token(
        self, config_dir: Path, account_id: str, token: str | None
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
            value=_normalize_secret(token),
        )

    def delete_bot_token(self, config_dir: Path, account_id: str) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
        )

    def can_persist_token(self) -> bool:
        return True

    def _migrate_legacy_keyring(self, config_dir: Path, account_id: str) -> str | None:
        if keyring is None:
            return None
        account_name = f"{config_dir.expanduser().resolve()}::{account_id}"
        try:
            legacy_value = keyring.get_password(
                _LEGACY_KEYRING_SERVICE_NAME, account_name
            )
        except Exception:
            return None
        normalized = _normalize_secret(legacy_value)
        if normalized is None:
            return None
        migrated = self._secret_store.migrate_legacy_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
            value=normalized,
        )
        if migrated:
            try:
                keyring.delete_password(_LEGACY_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return normalized
        return normalized


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
