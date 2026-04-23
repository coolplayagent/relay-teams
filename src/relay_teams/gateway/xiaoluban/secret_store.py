# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Optional

from relay_teams.secrets import AppSecretStore, get_secret_store

_NAMESPACE = "xiaoluban_account"
_FIELD_NAME = "token"


class XiaolubanSecretStore:
    def __init__(self, *, secret_store: Optional[AppSecretStore] = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_token(self, config_dir: Path, account_id: str) -> Optional[str]:
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
        )
        return _normalize_secret(value)

    def set_token(
        self,
        config_dir: Path,
        account_id: str,
        token: Optional[str],
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
            value=_normalize_secret(token),
        )

    def delete_token(self, config_dir: Path, account_id: str) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
        )

    @staticmethod
    def can_persist_token() -> bool:
        return True


_XIAOLUBAN_SECRET_STORE = XiaolubanSecretStore()


def get_xiaoluban_secret_store() -> XiaolubanSecretStore:
    return _XIAOLUBAN_SECRET_STORE


def _normalize_secret(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


__all__ = ["XiaolubanSecretStore", "get_xiaoluban_secret_store"]
