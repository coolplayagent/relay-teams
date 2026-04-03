# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from agent_teams.env.web_config_models import WebProvider
from agent_teams.secrets import AppSecretStore, get_secret_store

_LEGACY_KEYRING_SERVICE_NAME = "agent-teams.web-api-key"
_NAMESPACE = "web_config"
_OWNER_ID = "default"
_LEGACY_FIELD_NAME = "api_key"
_FIELD_NAME_BY_PROVIDER = {
    WebProvider.EXA: "exa_api_key",
    WebProvider.SEARXNG: "searxng_api_key",
}


class WebSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_api_key(self, config_dir: Path, provider: WebProvider) -> str | None:
        if provider == WebProvider.EXA:
            self._migrate_legacy_api_key(config_dir)
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME_BY_PROVIDER[provider],
        )
        return _normalize_secret(value)

    def set_api_key(
        self,
        config_dir: Path,
        provider: WebProvider,
        api_key: str | None,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME_BY_PROVIDER[provider],
            value=_normalize_secret(api_key),
        )

    def delete_api_key(self, config_dir: Path, provider: WebProvider) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME_BY_PROVIDER[provider],
        )

    def can_persist_api_key(self) -> bool:
        return True

    def _migrate_legacy_api_key(self, config_dir: Path) -> str | None:
        existing = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_LEGACY_FIELD_NAME,
        )
        migrated = self._migrate_legacy_keyring(config_dir)
        legacy_value = _normalize_secret(existing if existing is not None else migrated)
        if legacy_value is None:
            return None
        self._secret_store.migrate_legacy_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_FIELD_NAME_BY_PROVIDER[WebProvider.EXA],
            value=legacy_value,
        )
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=_OWNER_ID,
            field_name=_LEGACY_FIELD_NAME,
        )
        return legacy_value

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
            field_name=_LEGACY_FIELD_NAME,
            value=normalized,
        )
        if migrated:
            try:
                keyring.delete_password(_LEGACY_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return normalized
        return normalized


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
