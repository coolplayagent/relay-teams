# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from relay_teams.secrets import AppSecretStore, get_secret_store

_LEGACY_KEYRING_SERVICE_NAME = "agent-teams.proxy-password"
_NAMESPACE = "proxy_config"
_OWNER_ID = "default"
_FIELD_NAME = "password"


class ProxySecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_password(self, config_dir: Path) -> str | None:
        migrated = self._migrate_legacy_keyring(config_dir)
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME,
        )
        return _normalize_secret(value if value is not None else migrated)

    def set_password(self, config_dir: Path, password: str | None) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME,
            value=_normalize_secret(password),
        )

    def delete_password(self, config_dir: Path) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME,
        )

    def can_persist_password(self) -> bool:
        return True

    def backend_name(self) -> str:
        if self._secret_store.has_usable_keyring_backend():
            return "keyring"
        return "file"

    def _migrate_legacy_keyring(self, config_dir: Path) -> str | None:
        if keyring is None:
            return None
        account_name = str(config_dir.expanduser().resolve())
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
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME,
            value=normalized,
        )
        if migrated:
            try:
                keyring.delete_password(_LEGACY_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return normalized
        return normalized


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
