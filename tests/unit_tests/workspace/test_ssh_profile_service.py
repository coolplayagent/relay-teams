# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.secrets import AppSecretStore
from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileRepository,
    SshProfileSecretStore,
    SshProfileService,
)


class _FileOnlySecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_ssh_profile_service_stores_password_and_private_key_in_secret_store(
    tmp_path: Path,
) -> None:
    secret_store = SshProfileSecretStore(secret_store=_FileOnlySecretStore())
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=secret_store,
    )

    saved = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            password="secret",
            private_key=(
                "-----BEGIN OPENSSH PRIVATE KEY-----\r\n"
                "abc123\r\n"
                "-----END OPENSSH PRIVATE KEY-----\r\n"
            ),
            private_key_name="id_ed25519",
        ),
    )

    fetched = service.get_profile("prod")

    assert saved.has_password is True
    assert saved.has_private_key is True
    assert fetched.private_key_name == "id_ed25519"
    assert service.reveal_password("prod").password == "secret"
    assert secret_store.get_password(tmp_path, "prod") == "secret"
    assert secret_store.get_private_key(tmp_path, "prod") == (
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123\n-----END OPENSSH PRIVATE KEY-----"
    )


def test_ssh_profile_service_preserves_existing_secrets_and_deletes_them(
    tmp_path: Path,
) -> None:
    secret_store = SshProfileSecretStore(secret_store=_FileOnlySecretStore())
    service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
        secret_store=secret_store,
    )
    _ = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias",
            password="secret",
            private_key="-----BEGIN KEY-----\ncontent\n-----END KEY-----",
            private_key_name="id_rsa",
        ),
    )

    updated = service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(
            host="prod-alias-2",
            username="ops",
        ),
    )

    assert updated.host == "prod-alias-2"
    assert updated.has_password is True
    assert updated.has_private_key is True
    assert updated.private_key_name == "id_rsa"
    assert secret_store.get_password(tmp_path, "prod") == "secret"
    assert secret_store.get_private_key(tmp_path, "prod") is not None

    service.delete_profile("prod")

    with pytest.raises(KeyError):
        service.get_profile("prod")
    assert secret_store.get_password(tmp_path, "prod") is None
    assert secret_store.get_private_key(tmp_path, "prod") is None


def test_ssh_profile_config_rejects_whitespace_only_host() -> None:
    with pytest.raises(ValueError, match="host"):
        _ = SshProfileConfig(host="   ")
