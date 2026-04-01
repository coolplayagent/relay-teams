# -*- coding: utf-8 -*-
from __future__ import annotations

from json import loads
from pathlib import Path

from agent_teams.secrets import AppSecretStore, SecretIndexDocument, SecretIndexEntry
from agent_teams.secrets.secret_models import SecretCoordinate


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


class _FlakyKeyringSecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return True

    def _set_in_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
        value: str,
    ) -> None:
        _ = (config_dir, coordinate, value)
        raise RuntimeError("simulated keyring failure")


class _UnreadableKeyringSecretStore(AppSecretStore):
    def __init__(self) -> None:
        self.deleted_coordinates: list[SecretCoordinate] = []

    def has_usable_keyring_backend(self) -> bool:
        return True

    def _get_from_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
    ) -> str | None:
        _ = (config_dir, coordinate)
        return None

    def _delete_from_keyring(
        self,
        config_dir: Path,
        coordinate: SecretCoordinate,
    ) -> None:
        _ = config_dir
        self.deleted_coordinates.append(coordinate)


def test_set_secret_falls_back_to_shared_secrets_file(tmp_path: Path) -> None:
    store = _FileOnlySecretStore()

    store.set_secret(
        tmp_path,
        namespace="proxy_config",
        owner_id="default",
        field_name="password",
        value="secret",
    )

    assert (
        store.get_secret(
            tmp_path,
            namespace="proxy_config",
            owner_id="default",
            field_name="password",
        )
        == "secret"
    )
    payload = loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert payload["entries"] == [
        {
            "namespace": "proxy_config",
            "owner_id": "default",
            "field_name": "password",
            "storage": "file",
            "value": "secret",
        }
    ]


def test_set_secret_falls_back_to_file_when_keyring_write_fails(tmp_path: Path) -> None:
    store = _FlakyKeyringSecretStore()

    store.set_secret(
        tmp_path,
        namespace="proxy_config",
        owner_id="default",
        field_name="password",
        value="secret",
    )

    assert (
        store.get_secret(
            tmp_path,
            namespace="proxy_config",
            owner_id="default",
            field_name="password",
        )
        == "secret"
    )
    payload = loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert payload["entries"] == [
        {
            "namespace": "proxy_config",
            "owner_id": "default",
            "field_name": "password",
            "storage": "file",
            "value": "secret",
        }
    ]


def test_rename_owner_moves_file_backed_secrets(tmp_path: Path) -> None:
    store = _FileOnlySecretStore()
    store.set_secret(
        tmp_path,
        namespace="model_profile",
        owner_id="default",
        field_name="api_key",
        value="secret-key",
    )

    store.rename_owner(
        tmp_path,
        namespace="model_profile",
        from_owner_id="default",
        to_owner_id="renamed",
    )

    assert (
        store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="default",
            field_name="api_key",
        )
        is None
    )
    assert (
        store.get_secret(
            tmp_path,
            namespace="model_profile",
            owner_id="renamed",
            field_name="api_key",
        )
        == "secret-key"
    )


def test_rename_owner_preserves_unreadable_keyring_entry(tmp_path: Path) -> None:
    store = _UnreadableKeyringSecretStore()
    store._save_index(
        tmp_path,
        SecretIndexDocument(
            entries=(
                SecretIndexEntry(
                    namespace="model_profile",
                    owner_id="default",
                    field_name="api_key",
                    storage="keyring",
                ),
            )
        ),
    )

    store.rename_owner(
        tmp_path,
        namespace="model_profile",
        from_owner_id="default",
        to_owner_id="renamed",
    )

    payload = loads((tmp_path / "secrets.json").read_text(encoding="utf-8"))
    assert payload["entries"] == [
        {
            "namespace": "model_profile",
            "owner_id": "default",
            "field_name": "api_key",
            "storage": "keyring",
            "value": None,
        }
    ]
    assert store.deleted_coordinates == []


def test_delete_owner_removes_all_secret_fields(tmp_path: Path) -> None:
    store = _FileOnlySecretStore()
    store.set_secret(
        tmp_path,
        namespace="feishu_trigger",
        owner_id="trigger-a",
        field_name="app_secret",
        value="app-secret",
    )
    store.set_secret(
        tmp_path,
        namespace="feishu_trigger",
        owner_id="trigger-a",
        field_name="verification_token",
        value="verification-token",
    )

    store.delete_owner(
        tmp_path,
        namespace="feishu_trigger",
        owner_id="trigger-a",
    )

    assert (
        store.get_owner_secrets(
            tmp_path,
            namespace="feishu_trigger",
            owner_id="trigger-a",
        )
        == {}
    )
