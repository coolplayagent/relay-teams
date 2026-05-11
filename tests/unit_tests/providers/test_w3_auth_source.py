from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.providers.w3_auth_source import (
    W3_PASSWORD_FIELD,
    W3_SECRET_NAMESPACE,
    W3_SECRET_OWNER_ID,
    get_w3_credentials,
    require_w3_credentials,
)
from relay_teams.secrets import AppSecretStore


class _FileSecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


def test_get_w3_credentials_reads_username_and_password(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    _write_w3_config(tmp_path, '{"username": " user "}')
    secret_store.set_secret(
        tmp_path,
        namespace=W3_SECRET_NAMESPACE,
        owner_id=W3_SECRET_OWNER_ID,
        field_name=W3_PASSWORD_FIELD,
        value="secret",
    )

    credentials = get_w3_credentials(tmp_path, secret_store=secret_store)

    assert credentials is not None
    assert credentials.username == "user"
    assert credentials.password == "secret"


def test_get_w3_credentials_ignores_malformed_config(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    _write_w3_config(tmp_path, "{")

    assert get_w3_credentials(tmp_path, secret_store=secret_store) is None


def test_get_w3_credentials_ignores_blank_username(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    _write_w3_config(tmp_path, '{"username": "   "}')
    secret_store.set_secret(
        tmp_path,
        namespace=W3_SECRET_NAMESPACE,
        owner_id=W3_SECRET_OWNER_ID,
        field_name=W3_PASSWORD_FIELD,
        value="secret",
    )

    assert get_w3_credentials(tmp_path, secret_store=secret_store) is None


def test_require_w3_credentials_rejects_missing_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="W3 connector credentials are required"):
        require_w3_credentials(tmp_path, secret_store=_FileSecretStore())


def _write_w3_config(tmp_path: Path, content: str) -> None:
    config_dir = tmp_path / "connectors"
    config_dir.mkdir()
    (config_dir / "w3.json").write_text(content, encoding="utf-8")
