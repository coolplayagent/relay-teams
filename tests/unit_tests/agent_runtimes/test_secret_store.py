# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import relay_teams.agent_runtimes.secret_store as secret_store_module


class _LegacyKeyringFailure(RuntimeError):
    pass


class _FakeAppSecretStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str, str], str] = {}

    def get_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> str | None:
        return self.values.get((str(config_dir), namespace, owner_id, field_name))

    def set_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> None:
        if value is None:
            self.delete_secret(
                config_dir,
                namespace=namespace,
                owner_id=owner_id,
                field_name=field_name,
            )
            return
        self.values[(str(config_dir), namespace, owner_id, field_name)] = value

    def delete_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
    ) -> None:
        self.values.pop((str(config_dir), namespace, owner_id, field_name), None)

    def delete_owner(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
    ) -> None:
        prefix = (str(config_dir), namespace, owner_id)
        self.values = {
            key: value for key, value in self.values.items() if key[:3] != prefix
        }

    def migrate_legacy_secret(
        self,
        config_dir: Path,
        *,
        namespace: str,
        owner_id: str,
        field_name: str,
        value: str | None,
    ) -> bool:
        if value is None:
            return False
        key = (str(config_dir), namespace, owner_id, field_name)
        if key in self.values:
            return False
        self.values[key] = value
        return True


class _FailingLegacyKeyring:
    def get_password(self, _service_name: str, _account_name: str) -> str | None:
        raise _LegacyKeyringFailure

    def delete_password(self, _service_name: str, _account_name: str) -> None:
        raise AssertionError("delete_password should not be called")


class _DeletingFailureLegacyKeyring:
    def get_password(self, _service_name: str, _account_name: str) -> str | None:
        return " legacy-secret "

    def delete_password(self, _service_name: str, _account_name: str) -> None:
        raise _LegacyKeyringFailure


@pytest.fixture(autouse=True)
def fake_keyring_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        secret_store_module,
        "_KEYRING_ERRORS",
        (_LegacyKeyringFailure,),
    )


def test_secret_store_ignores_legacy_keyring_read_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secret_store_module, "keyring", _FailingLegacyKeyring())
    app_store = _FakeAppSecretStore()
    store = secret_store_module.ExternalAgentSecretStore(
        secret_store=cast(secret_store_module.AppSecretStore, app_store)
    )

    value = store.get_secret(
        config_dir=tmp_path,
        agent_id="agent-1",
        kind="env",
        name="API_KEY",
    )

    assert value is None
    assert app_store.values == {}


def test_secret_store_keeps_migrated_secret_when_legacy_delete_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        secret_store_module,
        "keyring",
        _DeletingFailureLegacyKeyring(),
    )
    app_store = _FakeAppSecretStore()
    store = secret_store_module.ExternalAgentSecretStore(
        secret_store=cast(secret_store_module.AppSecretStore, app_store)
    )

    value = store.get_secret(
        config_dir=tmp_path,
        agent_id="agent-1",
        kind="env",
        name="API_KEY",
    )

    assert value == "legacy-secret"
    assert (
        app_store.values[(str(tmp_path), "external_agent", "agent-1", "env:API_KEY")]
        == "legacy-secret"
    )
