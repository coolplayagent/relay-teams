# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.secrets import AppSecretStore, get_secret_store

_NAMESPACE = "workspace_ssh_profile"
_PASSWORD_FIELD = "password"
_PRIVATE_KEY_FIELD = "private_key"


class SshProfileSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_password(self, config_dir: Path, ssh_profile_id: str) -> str | None:
        return _normalize_secret(
            self._secret_store.get_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=ssh_profile_id,
                field_name=_PASSWORD_FIELD,
            )
        )

    def set_password(
        self,
        config_dir: Path,
        ssh_profile_id: str,
        password: str | None,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=ssh_profile_id,
            field_name=_PASSWORD_FIELD,
            value=_normalize_secret(password),
        )

    def get_private_key(self, config_dir: Path, ssh_profile_id: str) -> str | None:
        return _normalize_private_key(
            self._secret_store.get_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=ssh_profile_id,
                field_name=_PRIVATE_KEY_FIELD,
            )
        )

    def set_private_key(
        self,
        config_dir: Path,
        ssh_profile_id: str,
        private_key: str | None,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=ssh_profile_id,
            field_name=_PRIVATE_KEY_FIELD,
            value=_normalize_private_key(private_key),
        )

    def get_secret_flags(
        self,
        config_dir: Path,
        ssh_profile_id: str,
    ) -> tuple[bool, bool]:
        fields = set(
            self._secret_store.list_owner_fields(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=ssh_profile_id,
            )
        )
        return _PASSWORD_FIELD in fields, _PRIVATE_KEY_FIELD in fields

    def delete_profile_secrets(self, config_dir: Path, ssh_profile_id: str) -> None:
        self._secret_store.delete_owner(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=ssh_profile_id,
        )


def get_ssh_profile_secret_store() -> SshProfileSecretStore:
    return _SSH_PROFILE_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_private_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return normalized or None


_SSH_PROFILE_SECRET_STORE = SshProfileSecretStore()
