# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from relay_teams.secrets import AppSecretStore, get_secret_store

W3_CONNECTOR_ID = "w3"
W3_CONNECTOR_NAME = "W3"
W3_SECRET_NAMESPACE = "connector"
W3_SECRET_OWNER_ID = "w3"
W3_PASSWORD_FIELD = "password"
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class W3Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


def get_w3_credentials(
    config_dir: Path,
    *,
    secret_store: AppSecretStore | None = None,
) -> W3Credentials | None:
    username = _load_w3_username(config_dir)
    password = _get_w3_password(config_dir, secret_store=secret_store)
    if username is None or password is None:
        return None
    return W3Credentials(username=username, password=password)


def require_w3_credentials(
    config_dir: Path,
    *,
    secret_store: AppSecretStore | None = None,
) -> W3Credentials:
    credentials = get_w3_credentials(config_dir, secret_store=secret_store)
    if credentials is None:
        raise ValueError(
            "W3 connector credentials are required before using W3 auth source."
        )
    return credentials


def _load_w3_username(config_dir: Path) -> str | None:
    config_file = config_dir / "connectors" / "w3.json"
    if not config_file.exists():
        return None
    try:
        payload = _JSON_OBJECT_ADAPTER.validate_json(
            config_file.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, ValidationError):
        return None
    username = payload.get("username")
    if not isinstance(username, str):
        return None
    normalized = username.strip()
    return normalized or None


def _get_w3_password(
    config_dir: Path,
    *,
    secret_store: AppSecretStore | None,
) -> str | None:
    store = get_secret_store() if secret_store is None else secret_store
    return store.get_secret(
        config_dir,
        namespace=W3_SECRET_NAMESPACE,
        owner_id=W3_SECRET_OWNER_ID,
        field_name=W3_PASSWORD_FIELD,
    )
