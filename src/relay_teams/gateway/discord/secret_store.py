# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.secrets import AppSecretStore, get_secret_store

_NAMESPACE = "discord_account"
_FIELD_NAME = "bot_token"


class DiscordSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_bot_token(self, config_dir: Path, account_id: str) -> str | None:
        value = self._secret_store.get_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id.strip(),
            field_name=_FIELD_NAME,
        )
        return _normalize_secret(value)

    def set_bot_token(
        self,
        config_dir: Path,
        account_id: str,
        token: str | None,
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

    @staticmethod
    def can_persist_token() -> bool:
        return True


_DISCORD_SECRET_STORE = DiscordSecretStore()


def get_discord_secret_store() -> DiscordSecretStore:
    return _DISCORD_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized
