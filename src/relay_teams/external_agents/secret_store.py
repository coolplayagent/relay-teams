# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

from relay_teams.secrets import AppSecretStore, get_secret_store

_LEGACY_KEYRING_SERVICE_NAME = "agent-teams.external-agents"
_NAMESPACE = "external_agent"


class ExternalAgentSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def can_persist_secrets(self) -> bool:
        return True

    def get_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> str | None:
        migrated = self._migrate_legacy_keyring(
            config_dir=config_dir,
            agent_id=agent_id,
            kind=kind,
            name=name,
        )
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=agent_id.strip(),
            field_name=self._field_name(kind=kind, name=name),
        )
        return _normalize_secret(value if value is not None else migrated)

    def set_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
        value: str,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=agent_id.strip(),
            field_name=self._field_name(kind=kind, name=name),
            value=_normalize_secret(value),
        )

    def delete_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=agent_id.strip(),
            field_name=self._field_name(kind=kind, name=name),
        )

    def delete_agent(self, *, config_dir: Path, agent_id: str) -> None:
        self._secret_store.delete_owner(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=agent_id.strip(),
        )

    def _field_name(self, *, kind: str, name: str) -> str:
        return f"{kind.strip()}:{name.strip()}"

    def _migrate_legacy_keyring(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> str | None:
        if keyring is None:
            return None
        account_name = (
            f"{str(config_dir.expanduser().resolve())}:{agent_id.strip()}:"
            f"{kind.strip()}:{name.strip()}"
        )
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
            owner_id=agent_id.strip(),
            field_name=self._field_name(kind=kind, name=name),
            value=normalized,
        )
        if migrated:
            try:
                keyring.delete_password(_LEGACY_KEYRING_SERVICE_NAME, account_name)
            except Exception:
                return normalized
        return normalized


_EXTERNAL_AGENT_SECRET_STORE = ExternalAgentSecretStore()


def get_external_agent_secret_store() -> ExternalAgentSecretStore:
    return _EXTERNAL_AGENT_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
