# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

_KEYRING_SERVICE_NAME = "agent-teams.github-token"


class GitHubSecretStore:
    def get_token(self, config_dir: Path) -> str | None:
        if not self.can_persist_token():
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

    def set_token(self, config_dir: Path, token: str | None) -> None:
        normalized_token = _normalize_secret(token)
        if normalized_token is None:
            self.delete_token(config_dir)
            return

        if not self.can_persist_token():
            raise RuntimeError(
                "GitHub token persistence requires a usable system keyring backend."
            )

        assert keyring is not None
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
                normalized_token,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist GitHub token to the system keyring."
            ) from exc

    def delete_token(self, config_dir: Path) -> None:
        if not self.can_persist_token():
            return
        assert keyring is not None
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(config_dir),
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


_GITHUB_SECRET_STORE = GitHubSecretStore()


def get_github_secret_store() -> GitHubSecretStore:
    return _GITHUB_SECRET_STORE


def _normalize_secret(value: str | None) -> str | None:
    if value is None:
        return None
    normalized_value = value.strip()
    if not normalized_value:
        return None
    return normalized_value
