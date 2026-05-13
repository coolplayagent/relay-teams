from __future__ import annotations

import threading
from pathlib import Path

import pytest

from relay_teams.env import w3_auth_token_env as w3_auth_token_env_module
from relay_teams.env import (
    env_declares_w3_x_auth_token as package_env_declares_w3_x_auth_token,
)
from relay_teams.env import (
    is_w3_x_auth_token_env_name as package_is_w3_x_auth_token_env_name,
)
from relay_teams.env import (
    overlay_w3_x_auth_token_env as package_overlay_w3_x_auth_token_env,
)
from relay_teams.env import resolve_w3_x_auth_token as package_resolve_w3_x_auth_token
from relay_teams.env.w3_auth_token_env import (
    is_w3_x_auth_token_env_name,
    overlay_w3_x_auth_token_env,
    resolve_w3_x_auth_token,
)
from relay_teams.providers.maas_auth import MaaSAuthContext, MaaSLoginError
from relay_teams.providers.model_config import MaaSAuthConfig
from relay_teams.providers.w3_auth_source import (
    W3Credentials,
    W3_PASSWORD_FIELD,
    W3_SECRET_NAMESPACE,
    W3_SECRET_OWNER_ID,
)
from relay_teams.secrets import AppSecretStore


class _FileSecretStore(AppSecretStore):
    def has_usable_keyring_backend(self) -> bool:
        return False


class _TokenService:
    def __init__(
        self,
        *,
        token: str = "w3-token",
        error: Exception | None = None,
    ) -> None:
        self._token = token
        self._error = error
        self.calls: list[MaaSAuthConfig] = []

    async def get_auth_context(
        self,
        *,
        auth_config: MaaSAuthConfig,
        ssl_verify: bool | None,
        connect_timeout_seconds: float,
        force_refresh: bool = False,
    ) -> MaaSAuthContext:
        _ = ssl_verify
        _ = connect_timeout_seconds
        _ = force_refresh
        self.calls.append(auth_config)
        if self._error is not None:
            raise self._error
        return MaaSAuthContext(token=self._token)


@pytest.mark.parametrize(
    "name",
    ("X_AUTH_TOKEN", "x-auth-token", "X-Auth-Token", "xAuthToken"),
)
def test_is_w3_x_auth_token_env_name_matches_supported_variants(name: str) -> None:
    assert is_w3_x_auth_token_env_name(name) is True


@pytest.mark.parametrize("name", ("AUTH_TOKEN", "WEB_TOKEN"))
def test_is_w3_x_auth_token_env_name_rejects_other_tokens(name: str) -> None:
    assert is_w3_x_auth_token_env_name(name) is False


def test_w3_auth_token_env_helpers_are_exposed_from_env_package() -> None:
    assert package_env_declares_w3_x_auth_token({"X_AUTH_TOKEN": "x"}) is True
    assert package_is_w3_x_auth_token_env_name("xAuthToken") is True
    assert callable(package_overlay_w3_x_auth_token_env)
    assert callable(package_resolve_w3_x_auth_token)


@pytest.mark.asyncio
async def test_w3_auth_token_env_async_helpers_are_exposed_from_env_package(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(token="runtime-token")
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    token = await package_resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=token_service,
    )
    env = await package_overlay_w3_x_auth_token_env(
        {"X_AUTH_TOKEN": "placeholder"},
        declared_env={"X_AUTH_TOKEN": "placeholder"},
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=token_service,
    )

    assert token == "runtime-token"
    assert env == {"X_AUTH_TOKEN": "runtime-token"}


@pytest.mark.asyncio
async def test_resolve_w3_x_auth_token_uses_w3_credentials(tmp_path: Path) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(token="runtime-token")
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    token = await resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=token_service,
    )

    assert token == "runtime-token"
    assert len(token_service.calls) == 1
    assert token_service.calls[0].username == "w3-user"
    assert token_service.calls[0].password == "w3-password"


@pytest.mark.asyncio
async def test_resolve_w3_x_auth_token_reads_w3_credentials_off_event_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_service = _TokenService(token="runtime-token")
    main_thread_id = threading.get_ident()
    credential_thread_ids: list[int] = []

    def fake_get_w3_credentials(
        config_dir: Path,
        *,
        secret_store: AppSecretStore | None = None,
    ) -> W3Credentials:
        _ = config_dir, secret_store
        credential_thread_ids.append(threading.get_ident())
        return W3Credentials(username="w3-user", password="w3-password")

    monkeypatch.setattr(
        w3_auth_token_env_module,
        "get_w3_credentials",
        fake_get_w3_credentials,
    )

    token = await resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=_FileSecretStore(),
        token_service=token_service,
    )

    assert token == "runtime-token"
    assert credential_thread_ids
    assert main_thread_id not in credential_thread_ids


@pytest.mark.asyncio
async def test_resolve_w3_x_auth_token_returns_none_without_credentials(
    tmp_path: Path,
) -> None:
    token_service = _TokenService(token="runtime-token")

    token = await resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=_FileSecretStore(),
        token_service=token_service,
    )

    assert token is None
    assert token_service.calls == []


@pytest.mark.asyncio
async def test_resolve_w3_x_auth_token_returns_none_on_login_failure(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(
        error=MaaSLoginError("login failed", status_code=401),
    )
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    token = await resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=token_service,
    )

    assert token is None
    assert len(token_service.calls) == 1


@pytest.mark.asyncio
async def test_resolve_w3_x_auth_token_returns_none_on_generic_failure(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    token_service = _TokenService(error=RuntimeError("service unavailable"))
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    token = await resolve_w3_x_auth_token(
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=token_service,
    )

    assert token is None
    assert len(token_service.calls) == 1


@pytest.mark.asyncio
async def test_overlay_w3_x_auth_token_env_only_replaces_declared_matching_key(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    env = await overlay_w3_x_auth_token_env(
        {"X_AUTH_TOKEN": "placeholder", "AUTH_TOKEN": "keep"},
        declared_env={"X_AUTH_TOKEN": "placeholder", "AUTH_TOKEN": "keep"},
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=_TokenService(token="runtime-token"),
    )

    assert env == {"X_AUTH_TOKEN": "runtime-token", "AUTH_TOKEN": "keep"}


@pytest.mark.asyncio
async def test_overlay_w3_x_auth_token_env_skips_missing_declared_key(
    tmp_path: Path,
) -> None:
    token_service = _TokenService(token="runtime-token")

    env = await overlay_w3_x_auth_token_env(
        {"AUTH_TOKEN": "keep"},
        declared_env={"X_AUTH_TOKEN": "placeholder"},
        config_dir=tmp_path,
        secret_store=_FileSecretStore(),
        token_service=token_service,
    )

    assert env == {"AUTH_TOKEN": "keep"}
    assert token_service.calls == []


@pytest.mark.asyncio
async def test_overlay_w3_x_auth_token_env_preserves_env_when_token_missing(
    tmp_path: Path,
) -> None:
    token_service = _TokenService(token="runtime-token")

    env = await overlay_w3_x_auth_token_env(
        {"X_AUTH_TOKEN": "placeholder", "AUTH_TOKEN": "keep"},
        declared_env={"X_AUTH_TOKEN": "placeholder"},
        config_dir=tmp_path,
        secret_store=_FileSecretStore(),
        token_service=token_service,
    )

    assert env == {"X_AUTH_TOKEN": "placeholder", "AUTH_TOKEN": "keep"}
    assert token_service.calls == []


@pytest.mark.asyncio
async def test_overlay_w3_x_auth_token_env_removes_inherited_matching_variants(
    tmp_path: Path,
) -> None:
    secret_store = _FileSecretStore()
    _write_w3_credentials(tmp_path, secret_store=secret_store)

    env = await overlay_w3_x_auth_token_env(
        {
            "X_AUTH_TOKEN": "ambient",
            "x_auth_token": "placeholder",
            "AUTH_TOKEN": "keep",
        },
        declared_env={"x_auth_token": "placeholder"},
        config_dir=tmp_path,
        secret_store=secret_store,
        token_service=_TokenService(token="runtime-token"),
    )

    assert env == {"x_auth_token": "runtime-token", "AUTH_TOKEN": "keep"}


def _write_w3_credentials(
    config_dir: Path,
    *,
    secret_store: AppSecretStore,
) -> None:
    connectors_dir = config_dir / "connectors"
    connectors_dir.mkdir()
    (connectors_dir / "w3.json").write_text(
        '{"username": "w3-user"}',
        encoding="utf-8",
    )
    secret_store.set_secret(
        config_dir,
        namespace=W3_SECRET_NAMESPACE,
        owner_id=W3_SECRET_OWNER_ID,
        field_name=W3_PASSWORD_FIELD,
        value="w3-password",
    )
