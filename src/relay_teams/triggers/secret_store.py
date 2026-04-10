# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.secrets import AppSecretStore, get_secret_store

_NAMESPACE = "github_trigger_account"
_TOKEN_FIELD = "token"
_WEBHOOK_SECRET_FIELD = "webhook_secret"


class GitHubTriggerSecretStore:
    def __init__(self, *, secret_store: AppSecretStore | None = None) -> None:
        self._secret_store = (
            get_secret_store() if secret_store is None else secret_store
        )

    def get_token(self, config_dir: Path, *, account_id: str) -> str | None:
        return _normalize_secret(
            self._secret_store.get_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=account_id,
                field_name=_TOKEN_FIELD,
            )
        )

    def set_token(
        self, config_dir: Path, *, account_id: str, token: str | None
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id,
            field_name=_TOKEN_FIELD,
            value=_normalize_secret(token),
        )

    def delete_token(self, config_dir: Path, *, account_id: str) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id,
            field_name=_TOKEN_FIELD,
        )

    def get_webhook_secret(self, config_dir: Path, *, account_id: str) -> str | None:
        return _normalize_secret(
            self._secret_store.get_secret(
                config_dir,
                namespace=_NAMESPACE,
                owner_id=account_id,
                field_name=_WEBHOOK_SECRET_FIELD,
            )
        )

    def set_webhook_secret(
        self,
        config_dir: Path,
        *,
        account_id: str,
        webhook_secret: str | None,
    ) -> None:
        self._secret_store.set_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id,
            field_name=_WEBHOOK_SECRET_FIELD,
            value=_normalize_secret(webhook_secret),
        )

    def delete_webhook_secret(self, config_dir: Path, *, account_id: str) -> None:
        self._secret_store.delete_secret(
            config_dir,
            namespace=_NAMESPACE,
            owner_id=account_id,
            field_name=_WEBHOOK_SECRET_FIELD,
        )


_GITHUB_TRIGGER_SECRET_STORE = GitHubTriggerSecretStore()


def get_github_trigger_secret_store() -> GitHubTriggerSecretStore:
    return _GITHUB_TRIGGER_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


__all__ = [
    "GitHubTriggerSecretStore",
    "get_github_trigger_secret_store",
]
