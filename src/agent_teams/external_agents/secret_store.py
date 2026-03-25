# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

try:
    import keyring
except Exception:  # pragma: no cover - import availability depends on environment
    keyring = None

_KEYRING_SERVICE_NAME = "agent-teams.external-agents"


class ExternalAgentSecretStore:
    def can_persist_secrets(self) -> bool:
        backend = self._get_backend()
        if backend is None:
            return False
        try:
            return float(getattr(backend, "priority", 0.0)) > 0
        except (TypeError, ValueError):
            return False

    def get_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> str | None:
        if not self.can_persist_secrets():
            return None
        assert keyring is not None
        try:
            return _normalize_secret(
                keyring.get_password(
                    _KEYRING_SERVICE_NAME,
                    self._account_name(
                        config_dir=config_dir,
                        agent_id=agent_id,
                        kind=kind,
                        name=name,
                    ),
                )
            )
        except Exception:
            return None

    def set_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
        value: str,
    ) -> None:
        normalized = _normalize_secret(value)
        if normalized is None:
            self.delete_secret(
                config_dir=config_dir,
                agent_id=agent_id,
                kind=kind,
                name=name,
            )
            return
        if not self.can_persist_secrets():
            raise RuntimeError(
                "External agent secret persistence requires a usable system keyring backend."
            )
        assert keyring is not None
        try:
            keyring.set_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(
                    config_dir=config_dir,
                    agent_id=agent_id,
                    kind=kind,
                    name=name,
                ),
                normalized,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to persist external agent secrets to the system keyring."
            ) from exc

    def delete_secret(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> None:
        if not self.can_persist_secrets():
            return
        assert keyring is not None
        try:
            keyring.delete_password(
                _KEYRING_SERVICE_NAME,
                self._account_name(
                    config_dir=config_dir,
                    agent_id=agent_id,
                    kind=kind,
                    name=name,
                ),
            )
        except Exception:
            return

    def delete_agent(self, *, config_dir: Path, agent_id: str) -> None:
        if not self.can_persist_secrets():
            return
        for kind in ("env", "header"):
            prefix = f"{self._config_prefix(config_dir, agent_id, kind)}:"
            backend = self._iter_backend_entries()
            if backend is None:
                return
            for account_name in backend:
                if not account_name.startswith(prefix):
                    continue
                try:
                    assert keyring is not None
                    keyring.delete_password(_KEYRING_SERVICE_NAME, account_name)
                except Exception:
                    continue

    def _account_name(
        self,
        *,
        config_dir: Path,
        agent_id: str,
        kind: str,
        name: str,
    ) -> str:
        return f"{self._config_prefix(config_dir, agent_id, kind)}:{name.strip()}"

    def _config_prefix(self, config_dir: Path, agent_id: str, kind: str) -> str:
        return (
            f"{str(config_dir.expanduser().resolve())}:"
            f"{agent_id.strip()}:{kind.strip()}"
        )

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

    def _iter_backend_entries(self) -> tuple[str, ...] | None:
        backend = self._get_backend()
        if backend is None:
            return None
        keyring_dict = getattr(backend, "keyring_dict", None)
        if not isinstance(keyring_dict, dict):
            return None
        service_entries = keyring_dict.get(_KEYRING_SERVICE_NAME)
        if not isinstance(service_entries, dict):
            return None
        return tuple(
            str(account_name)
            for account_name in service_entries.keys()
            if isinstance(account_name, str)
        )


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
