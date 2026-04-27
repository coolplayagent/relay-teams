# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import cast
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import relay_teams.providers.codeagent_auth as codeagent_auth_module
from relay_teams.providers.codeagent_auth import (
    CodeAgentOAuthError,
    CodeAgentOAuthTokenResult,
    CodeAgentTokenService,
    build_codeagent_request_headers,
    build_codeagent_authorization_url,
    clear_codeagent_oauth_session_store,
    clear_codeagent_token_service_cache,
    create_codeagent_oauth_session,
    get_codeagent_oauth_tokens,
    is_codeagent_chat_completion_request,
    save_codeagent_oauth_tokens,
)
from relay_teams.providers.model_config import (
    CodeAgentAuthConfig,
    DEFAULT_CODEAGENT_BASE_URL,
    DEFAULT_CODEAGENT_CLIENT_ID,
    DEFAULT_CODEAGENT_SCOPE,
    DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    ModelEndpointConfig,
    ProviderType,
)


def test_build_codeagent_authorization_url_uses_hardcoded_sso_base_url() -> None:
    url = build_codeagent_authorization_url(
        base_url="https://codeagent.example/codeAgentPro",
        client_id="codeagent-client",
        scope="1000:1002",
        scope_resource="devuc",
        redirect_url="https://codeagent.example/callback?client_code=client-code",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert url.startswith(
        "https://ssoproxysvr.cd-cloud-ssoproxysvr.szv.dragon.tools.huawei.com"
        "/ssoproxysvr/oauth2/authorize?"
    )
    assert query["client_id"] == ["codeagent-client"]
    assert query["redirect_uri"] == [
        "https://codeagent.example/callback?client_code=client-code"
    ]
    assert query["scope"] == ["1000:1002"]
    assert query["response_type"] == ["code"]
    assert query["scope_resource"] == ["devuc"]


def test_create_codeagent_oauth_session_uses_client_code_callback() -> None:
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )

    assert len(session.client_code) == 32
    assert session.callback_url == (
        f"{DEFAULT_CODEAGENT_BASE_URL}/codeAgent/oauth/callback"
        f"?client_code={session.client_code}"
    )
    assert session.state == session.client_code


def test_codeagent_token_result_tracks_token_expiry() -> None:
    expires_at = datetime.now(UTC) + timedelta(hours=1)
    token_result = CodeAgentOAuthTokenResult(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=expires_at,
    )

    assert token_result.access_token == "access-token"
    assert token_result.refresh_token == "refresh-token"
    assert token_result.expires_at == expires_at


def test_poll_token_sync_posts_client_code_json(monkeypatch) -> None:
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )
    captured: dict[str, object] = {}

    class _FakeHttpClient:
        def __enter__(self) -> "_FakeHttpClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(
                200,
                json={
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": "3600",
                },
            )

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        lambda **kwargs: _FakeHttpClient(),
    )

    token_result = CodeAgentTokenService().poll_token_sync(
        session=session,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert token_result is not None
    assert token_result.access_token == "access-token"
    assert captured["url"] == f"{DEFAULT_CODEAGENT_BASE_URL}/codeAgent/oauth/getToken"
    assert captured["json"] == {
        "clientCode": session.client_code,
        "redirectUrl": session.callback_url,
    }
    assert captured["headers"] == {"Content-Type": "application/json"}


def test_poll_token_sync_returns_none_until_token_available(monkeypatch) -> None:
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )

    class _FakeHttpClient:
        def __enter__(self) -> "_FakeHttpClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            _ = url
            _ = json
            _ = headers
            return httpx.Response(200, json={"message": "pending"})

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        lambda **kwargs: _FakeHttpClient(),
    )

    assert (
        CodeAgentTokenService().poll_token_sync(
            session=session,
            ssl_verify=None,
            connect_timeout_seconds=15.0,
        )
        is None
    )


def test_poll_token_sync_raises_for_http_error_response(monkeypatch) -> None:
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )

    class _FakeHttpClient:
        def __enter__(self) -> "_FakeHttpClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            _ = (url, json, headers)
            return httpx.Response(
                502,
                json={"message": "oauth upstream unavailable"},
            )

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        lambda **kwargs: _FakeHttpClient(),
    )

    with pytest.raises(CodeAgentOAuthError, match="oauth upstream unavailable"):
        CodeAgentTokenService().poll_token_sync(
            session=session,
            ssl_verify=None,
            connect_timeout_seconds=15.0,
        )


def test_codeagent_token_service_uses_configured_access_token_first(
    monkeypatch,
) -> None:
    def build_client(**kwargs: object) -> object:
        _ = kwargs
        raise AssertionError("refresh endpoint should not be called")

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        build_client,
    )

    token = CodeAgentTokenService().get_token_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(
            access_token="fresh-access-token",
            refresh_token="refresh-token",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert token == "fresh-access-token"


def test_codeagent_token_service_get_token_result_sync_uses_cached_result() -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")
    token_result = CodeAgentOAuthTokenResult(
        access_token="cached-access-token",
        refresh_token="cached-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    service._tokens[cache_key] = codeagent_auth_module._CodeAgentTokenRecord(
        token_result=token_result
    )

    result = service.get_token_result_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert result == token_result


def test_codeagent_token_service_get_token_result_sync_rechecks_cache_inside_lock(
    monkeypatch,
) -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")
    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    token_result = CodeAgentOAuthTokenResult(
        access_token="late-access-token",
        refresh_token="late-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class _PrimingLock:
        def __enter__(self) -> "_PrimingLock":
            service._tokens[cache_key] = codeagent_auth_module._CodeAgentTokenRecord(
                token_result=token_result
            )
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(
        service,
        "refresh_token_sync",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("refresh endpoint should not be called")
        ),
    )
    service._sync_locks[cache_key] = cast(Lock, _PrimingLock())

    result = service.get_token_result_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert result == token_result


def test_codeagent_token_service_cache_key_includes_secret_owner_id() -> None:
    service = CodeAgentTokenService()
    profile_a = CodeAgentAuthConfig(
        refresh_token="shared-refresh-token"
    ).with_secret_owner(
        config_dir=Path("."),
        owner_id="profile-a",
    )
    profile_b = CodeAgentAuthConfig(
        refresh_token="shared-refresh-token"
    ).with_secret_owner(
        config_dir=Path("."),
        owner_id="profile-b",
    )

    profile_a_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=profile_a,
    )
    profile_b_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=profile_b,
    )

    assert profile_a_key != profile_b_key


def test_codeagent_token_service_refreshes_with_rotated_refresh_token(
    monkeypatch,
) -> None:
    clear_codeagent_oauth_session_store()
    clear_codeagent_token_service_cache()
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )
    save_codeagent_oauth_tokens(
        state=session.state,
        token_result=CodeAgentOAuthTokenResult(
            access_token="cached-access-token",
            refresh_token="rotated-refresh-token",
            expires_at=datetime.now(UTC) - timedelta(minutes=5),
        ),
    )
    captured_refresh_tokens: list[str] = []
    refresh_responses = iter(
        (
            {
                "access_token": "refreshed-access-token",
                "refresh_token": "newly-rotated-refresh-token",
                "expires_in": "3600",
            },
            {
                "access_token": "refreshed-access-token-2",
                "refresh_token": "newly-rotated-refresh-token-2",
                "expires_in": "3600",
            },
        )
    )

    class _FakeHttpClient:
        def __enter__(self) -> "_FakeHttpClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            _ = (url, headers)
            payload = json
            assert isinstance(payload, dict)
            refresh_token = payload.get("refresh_token")
            assert isinstance(refresh_token, str)
            captured_refresh_tokens.append(refresh_token)
            return httpx.Response(200, json=next(refresh_responses))

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        lambda **kwargs: _FakeHttpClient(),
    )

    service = CodeAgentTokenService()
    token_result = service.get_token_result_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(
            refresh_token="stale-refresh-token",
            oauth_session_id=session.auth_session_id,
        ),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        force_refresh=True,
    )
    refreshed_again = service.get_token_result_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(
            refresh_token="stale-refresh-token",
            oauth_session_id=session.auth_session_id,
        ),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        force_refresh=True,
    )

    assert captured_refresh_tokens == [
        "stale-refresh-token",
        "newly-rotated-refresh-token",
    ]
    assert token_result.access_token == "refreshed-access-token"
    assert token_result.refresh_token == "newly-rotated-refresh-token"
    assert refreshed_again.access_token == "refreshed-access-token-2"
    assert refreshed_again.refresh_token == "newly-rotated-refresh-token-2"
    session_tokens = get_codeagent_oauth_tokens(session.auth_session_id)
    assert session_tokens is not None
    assert session_tokens.refresh_token == "newly-rotated-refresh-token-2"
    clear_codeagent_oauth_session_store()
    clear_codeagent_token_service_cache()


def test_codeagent_token_service_persists_rotated_tokens_for_secret_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_entries: list[tuple[Path, str, str, str, str]] = []

    class _FakeSecretStore:
        def set_secret(
            self,
            config_dir: Path,
            *,
            namespace: str,
            owner_id: str,
            field_name: str,
            value: str,
        ) -> None:
            captured_entries.append(
                (config_dir, namespace, owner_id, field_name, value)
            )

    class _FakeHttpClient:
        def __enter__(self) -> "_FakeHttpClient":
            return self

        def __exit__(self, *exc_info: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            _ = (url, json, headers)
            return httpx.Response(
                200,
                json={
                    "access_token": "rotated-access-token",
                    "refresh_token": "rotated-refresh-token",
                    "expires_in": "3600",
                },
            )

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.get_secret_store",
        lambda: _FakeSecretStore(),
    )
    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_sync_http_client",
        lambda **kwargs: _FakeHttpClient(),
    )

    token_result = CodeAgentTokenService().get_token_result_sync(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(
            refresh_token="stale-refresh-token",
        ).with_secret_owner(
            config_dir=tmp_path,
            owner_id="codeagent-profile",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        force_refresh=True,
    )

    assert token_result.refresh_token == "rotated-refresh-token"
    assert captured_entries == [
        (
            tmp_path,
            "model_profile",
            "codeagent-profile",
            "codeagent_access_token",
            "rotated-access-token",
        ),
        (
            tmp_path,
            "model_profile",
            "codeagent-profile",
            "codeagent_refresh_token",
            "rotated-refresh-token",
        ),
    ]


def test_codeagent_auth_config_forces_builtin_oauth_values() -> None:
    auth_config = CodeAgentAuthConfig(
        client_id="custom-client",
        scope="custom-scope",
        scope_resource="custom-resource",
        refresh_token="refresh-token",
    )

    assert auth_config.client_id == DEFAULT_CODEAGENT_CLIENT_ID
    assert auth_config.scope == DEFAULT_CODEAGENT_SCOPE
    assert auth_config.scope_resource == DEFAULT_CODEAGENT_SCOPE_RESOURCE


def test_codeagent_endpoint_config_forces_builtin_base_url() -> None:
    config = ModelEndpointConfig(
        provider=ProviderType.CODEAGENT,
        model="codeagent-chat",
        base_url="https://custom.example/codeAgentPro",
        codeagent_auth=CodeAgentAuthConfig(refresh_token="refresh-token"),
    )

    assert config.base_url == DEFAULT_CODEAGENT_BASE_URL


def test_is_codeagent_chat_completion_request_matches_sdk_path() -> None:
    request = httpx.Request(
        "POST",
        "https://codeagent.example/codeAgentPro/chat/completions",
    )

    assert is_codeagent_chat_completion_request(request) is True


def test_build_codeagent_request_headers_uses_access_token() -> None:
    headers = build_codeagent_request_headers(
        token="access-token",
        content_type="application/json",
        accept="text/event-stream",
    )

    assert headers["X-Auth-Token"] == "access-token"
    assert headers["app-id"] == "CodeAgent2.0"
    assert headers["User-Agent"] == "AgentKernel/1.0"
    assert headers["gray"] == "false"
    assert headers["oc-heartbeat"] == "1"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "text/event-stream"
    assert headers["X-snap-traceid"]
    assert headers["X-session-id"].startswith("ses_")


def test_codeagent_token_service_refresh_token_sync_requires_refresh_token() -> None:
    with pytest.raises(
        CodeAgentOAuthError,
        match="CodeAgent refresh token is not configured.",
    ):
        CodeAgentTokenService().refresh_token_sync(
            base_url=DEFAULT_CODEAGENT_BASE_URL,
            auth_config=CodeAgentAuthConfig(),
            ssl_verify=None,
            connect_timeout_seconds=15.0,
        )


@pytest.mark.asyncio
async def test_codeagent_token_service_refresh_token_requires_refresh_token() -> None:
    with pytest.raises(
        CodeAgentOAuthError,
        match="CodeAgent refresh token is not configured.",
    ):
        await CodeAgentTokenService().refresh_token(
            base_url=DEFAULT_CODEAGENT_BASE_URL,
            auth_config=CodeAgentAuthConfig(),
            ssl_verify=None,
            connect_timeout_seconds=15.0,
        )


@pytest.mark.asyncio
async def test_codeagent_token_service_get_token_uses_cached_async_result() -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")
    token_result = CodeAgentOAuthTokenResult(
        access_token="cached-access-token",
        refresh_token="cached-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    service._tokens[cache_key] = codeagent_auth_module._CodeAgentTokenRecord(
        token_result=token_result,
    )

    token = await service.get_token(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert token == "cached-access-token"


@pytest.mark.asyncio
async def test_codeagent_token_service_get_token_result_rechecks_async_cache_inside_lock(
    monkeypatch,
) -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")
    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    token_result = CodeAgentOAuthTokenResult(
        access_token="late-async-access-token",
        refresh_token="late-async-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class _PrimingAsyncLock:
        async def __aenter__(self) -> "_PrimingAsyncLock":
            service._tokens[cache_key] = codeagent_auth_module._CodeAgentTokenRecord(
                token_result=token_result
            )
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    async def fail_refresh(**_kwargs: object) -> CodeAgentOAuthTokenResult:
        raise AssertionError("refresh endpoint should not be called")

    monkeypatch.setattr(service, "refresh_token", fail_refresh)
    service._async_locks[cache_key] = cast(asyncio.Lock, _PrimingAsyncLock())

    result = await service.get_token_result(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert result == token_result


@pytest.mark.asyncio
async def test_codeagent_token_service_get_token_result_uses_config_tokens_async(
    monkeypatch,
) -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(
        access_token="configured-access-token",
        refresh_token="configured-refresh-token",
    )

    async def fail_refresh(**_kwargs: object) -> CodeAgentOAuthTokenResult:
        raise AssertionError("refresh endpoint should not be called")

    monkeypatch.setattr(service, "refresh_token", fail_refresh)

    result = await service.get_token_result(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    assert result.access_token == "configured-access-token"
    assert service._tokens[cache_key].token_result == result


@pytest.mark.asyncio
async def test_codeagent_token_service_get_token_result_refreshes_and_stores_async_result(
    monkeypatch,
) -> None:
    service = CodeAgentTokenService()
    auth_config = CodeAgentAuthConfig(refresh_token="refresh-token")
    refreshed_result = CodeAgentOAuthTokenResult(
        access_token="refreshed-access-token",
        refresh_token="refreshed-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    async def fake_refresh_token(**_kwargs: object) -> CodeAgentOAuthTokenResult:
        return refreshed_result

    monkeypatch.setattr(service, "refresh_token", fake_refresh_token)

    result = await service.get_token_result(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        force_refresh=True,
    )

    cache_key = service._cache_key(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=auth_config,
    )
    assert result == refreshed_result
    assert service._tokens[cache_key].token_result == refreshed_result


@pytest.mark.asyncio
async def test_codeagent_token_service_refresh_token_async_posts_refresh_payload(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeAsyncHttpClient:
        async def __aenter__(self) -> "_FakeAsyncHttpClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: object,
            headers: dict[str, str],
        ) -> httpx.Response:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(
                200,
                json={
                    "access_token": "async-access-token",
                    "refresh_token": "async-refresh-token",
                    "expires_in": "3600",
                },
            )

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.create_async_http_client",
        lambda **kwargs: _FakeAsyncHttpClient(),
    )

    token_result = await CodeAgentTokenService().refresh_token(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
    )

    assert token_result.access_token == "async-access-token"
    assert token_result.refresh_token == "async-refresh-token"
    assert captured["url"] == (
        f"{DEFAULT_CODEAGENT_BASE_URL}/codeAgent/oauth/refreshToken"
    )
    assert captured["headers"] == {"Content-Type": "application/json"}


def test_codeagent_token_service_store_token_result_persists_when_session_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_entries: list[tuple[Path, str, str, str, str]] = []

    class _FakeSecretStore:
        def set_secret(
            self,
            config_dir: Path,
            *,
            namespace: str,
            owner_id: str,
            field_name: str,
            value: str,
        ) -> None:
            captured_entries.append(
                (config_dir, namespace, owner_id, field_name, value)
            )

    monkeypatch.setattr(
        "relay_teams.providers.codeagent_auth.get_secret_store",
        lambda: _FakeSecretStore(),
    )

    CodeAgentTokenService()._store_token_result(
        cache_key="cache-key",
        auth_config=CodeAgentAuthConfig(
            oauth_session_id="missing-session",
        ).with_secret_owner(
            config_dir=tmp_path,
            owner_id="codeagent-profile",
        ),
        token_result=CodeAgentOAuthTokenResult(
            access_token="stored-access-token",
            refresh_token="stored-refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    assert captured_entries == [
        (
            tmp_path,
            "model_profile",
            "codeagent-profile",
            "codeagent_access_token",
            "stored-access-token",
        ),
        (
            tmp_path,
            "model_profile",
            "codeagent-profile",
            "codeagent_refresh_token",
            "stored-refresh-token",
        ),
    ]


def test_codeagent_request_auth_sync_flow_retries_after_unauthorized() -> None:
    calls: list[bool] = []

    class _FakeTokenService:
        def get_token_sync(
            self,
            *,
            base_url: str,
            auth_config: CodeAgentAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            _ = (base_url, auth_config, ssl_verify, connect_timeout_seconds)
            calls.append(force_refresh)
            return "retry-token" if force_refresh else "initial-token"

    request = httpx.Request(
        "POST",
        "https://codeagent.example/codeAgentPro/chat/completions",
        content=b'{"model":"codeagent-chat"}',
    )
    auth = codeagent_auth_module.CodeAgentRequestAuth(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        token_service=cast(CodeAgentTokenService, _FakeTokenService()),
    )

    flow = auth.sync_auth_flow(request)
    first_request = next(flow)
    retry_request = flow.send(httpx.Response(401, request=first_request))

    assert first_request.headers["X-Auth-Token"] == "initial-token"
    assert retry_request.headers["X-Auth-Token"] == "retry-token"
    assert calls == [False, True]

    with pytest.raises(StopIteration):
        flow.send(httpx.Response(200, request=retry_request))


def test_codeagent_request_auth_sync_flow_stops_after_success() -> None:
    calls: list[bool] = []

    class _FakeTokenService:
        def get_token_sync(
            self,
            *,
            base_url: str,
            auth_config: CodeAgentAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            _ = (base_url, auth_config, ssl_verify, connect_timeout_seconds)
            calls.append(force_refresh)
            return "initial-token"

    request = httpx.Request(
        "POST",
        "https://codeagent.example/codeAgentPro/chat/completions",
        content=b'{"model":"codeagent-chat"}',
    )
    auth = codeagent_auth_module.CodeAgentRequestAuth(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        token_service=cast(CodeAgentTokenService, _FakeTokenService()),
    )

    flow = auth.sync_auth_flow(request)
    first_request = next(flow)

    with pytest.raises(StopIteration):
        flow.send(httpx.Response(200, request=first_request))

    assert first_request.headers["X-Auth-Token"] == "initial-token"
    assert calls == [False]


@pytest.mark.asyncio
async def test_codeagent_request_auth_async_flow_retries_after_unauthorized() -> None:
    calls: list[bool] = []

    class _FakeTokenService:
        async def get_token(
            self,
            *,
            base_url: str,
            auth_config: CodeAgentAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            _ = (base_url, auth_config, ssl_verify, connect_timeout_seconds)
            calls.append(force_refresh)
            return "retry-token" if force_refresh else "initial-token"

    request = httpx.Request(
        "POST",
        "https://codeagent.example/codeAgentPro/chat/completions",
        content=b'{"model":"codeagent-chat"}',
    )
    auth = codeagent_auth_module.CodeAgentRequestAuth(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        token_service=cast(CodeAgentTokenService, _FakeTokenService()),
    )

    flow = auth.async_auth_flow(request)
    first_request = await flow.__anext__()
    retry_request = await flow.asend(httpx.Response(403, request=first_request))

    assert first_request.headers["X-Auth-Token"] == "initial-token"
    assert retry_request.headers["X-Auth-Token"] == "retry-token"
    assert calls == [False, True]

    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(200, request=retry_request))


@pytest.mark.asyncio
async def test_codeagent_request_auth_async_flow_stops_after_success() -> None:
    calls: list[bool] = []

    class _FakeTokenService:
        async def get_token(
            self,
            *,
            base_url: str,
            auth_config: CodeAgentAuthConfig,
            ssl_verify: bool | None,
            connect_timeout_seconds: float,
            force_refresh: bool = False,
        ) -> str:
            _ = (base_url, auth_config, ssl_verify, connect_timeout_seconds)
            calls.append(force_refresh)
            return "initial-token"

    request = httpx.Request(
        "POST",
        "https://codeagent.example/codeAgentPro/chat/completions",
        content=b'{"model":"codeagent-chat"}',
    )
    auth = codeagent_auth_module.CodeAgentRequestAuth(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
        ssl_verify=None,
        connect_timeout_seconds=15.0,
        token_service=cast(CodeAgentTokenService, _FakeTokenService()),
    )

    flow = auth.async_auth_flow(request)
    first_request = await flow.__anext__()

    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(200, request=first_request))

    assert first_request.headers["X-Auth-Token"] == "initial-token"
    assert calls == [False]


def test_consume_codeagent_oauth_tokens_returns_tokens_only_once() -> None:
    clear_codeagent_oauth_session_store()
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )
    token_result = CodeAgentOAuthTokenResult(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    save_codeagent_oauth_tokens(state=session.state, token_result=token_result)

    consumed = codeagent_auth_module.consume_codeagent_oauth_tokens(
        session.auth_session_id
    )
    consumed_again = codeagent_auth_module.consume_codeagent_oauth_tokens(
        session.auth_session_id
    )

    assert consumed == token_result
    assert consumed_again is None


def test_get_codeagent_oauth_session_by_state_returns_saved_session() -> None:
    clear_codeagent_oauth_session_store()
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )

    assert (
        codeagent_auth_module.get_codeagent_oauth_session_by_state(session.state)
        == session
    )


def test_extract_error_code_reads_nested_error_code() -> None:
    payload = {
        "error": {
            "code": " DEV.00000001 ",
        }
    }

    assert codeagent_auth_module._extract_error_code(payload) == "DEV.00000001"


def test_get_codeagent_oauth_session_by_state_returns_none_for_missing_state() -> None:
    clear_codeagent_oauth_session_store()

    assert codeagent_auth_module.get_codeagent_oauth_session_by_state("missing") is None


def test_save_codeagent_oauth_tokens_for_unknown_session_raises() -> None:
    with pytest.raises(
        CodeAgentOAuthError, match="Unknown or expired CodeAgent OAuth session."
    ):
        codeagent_auth_module.save_codeagent_oauth_tokens_for_session(
            auth_session_id="missing-session",
            token_result=CodeAgentOAuthTokenResult(
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )


def test_save_codeagent_oauth_tokens_for_unknown_state_raises() -> None:
    with pytest.raises(
        CodeAgentOAuthError, match="Unknown or expired CodeAgent OAuth state."
    ):
        codeagent_auth_module.save_codeagent_oauth_tokens(
            state="missing-state",
            token_result=CodeAgentOAuthTokenResult(
                access_token="access-token",
                refresh_token="refresh-token",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )


def test_expired_codeagent_oauth_sessions_are_purged() -> None:
    clear_codeagent_oauth_session_store()
    session = create_codeagent_oauth_session(
        base_url=DEFAULT_CODEAGENT_BASE_URL,
        client_id=DEFAULT_CODEAGENT_CLIENT_ID,
        scope=DEFAULT_CODEAGENT_SCOPE,
        scope_resource=DEFAULT_CODEAGENT_SCOPE_RESOURCE,
    )
    token_result = CodeAgentOAuthTokenResult(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    save_codeagent_oauth_tokens(state=session.state, token_result=token_result)
    expired_session = session.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    store = codeagent_auth_module._CODEAGENT_OAUTH_SESSION_STORE
    store._sessions_by_id[session.auth_session_id] = expired_session

    assert (
        codeagent_auth_module.get_codeagent_oauth_session(session.auth_session_id)
        is None
    )
    assert (
        codeagent_auth_module.get_codeagent_oauth_session_by_state(session.state)
        is None
    )
    assert (
        codeagent_auth_module.get_codeagent_oauth_tokens(session.auth_session_id)
        is None
    )


def test_generate_client_code_falls_back_to_token_hex(monkeypatch) -> None:
    monkeypatch.setattr(
        codeagent_auth_module,
        "uuid4",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        codeagent_auth_module, "token_hex", lambda size: "fallback-client-code"
    )

    assert codeagent_auth_module._generate_client_code() == "fallback-client-code"


def test_codeagent_token_service_marks_expiring_tokens_for_refresh() -> None:
    token_result = CodeAgentOAuthTokenResult(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )

    assert CodeAgentTokenService()._should_refresh(token_result) is True


def test_build_token_result_uses_response_text_for_http_errors() -> None:
    response = httpx.Response(502, content=b"oauth upstream unavailable")

    with pytest.raises(CodeAgentOAuthError, match="oauth upstream unavailable"):
        codeagent_auth_module._build_token_result(
            response,
            fallback_refresh_token=None,
        )


def test_build_token_result_extracts_codeagent_auth_invalid_error_details() -> None:
    response = httpx.Response(
        401,
        json={
            "error_code": "DEV.00000001",
            "error_msg": "未识别到用户认证信息",
        },
    )

    with pytest.raises(CodeAgentOAuthError) as exc_info:
        codeagent_auth_module._build_token_result(
            response,
            fallback_refresh_token=None,
        )

    assert str(exc_info.value) == "未识别到用户认证信息"
    assert exc_info.value.error_code == "DEV.00000001"
    assert exc_info.value.auth_invalid is True


def test_build_polled_token_result_returns_none_for_non_200_response() -> None:
    response = httpx.Response(202, json={"message": "pending"})

    assert codeagent_auth_module._build_polled_token_result(response) is None


def test_build_polled_token_result_raises_for_200_error_payload() -> None:
    response = httpx.Response(200, json={"message": "expired session"})

    with pytest.raises(CodeAgentOAuthError, match="expired session"):
        codeagent_auth_module._build_polled_token_result(response)


def test_build_polled_token_result_requires_refresh_token() -> None:
    response = httpx.Response(
        200,
        json={"access_token": "access-token"},
    )

    with pytest.raises(
        CodeAgentOAuthError,
        match="did not include refresh_token",
    ):
        codeagent_auth_module._build_polled_token_result(response)


def test_build_token_result_uses_fallback_refresh_token() -> None:
    response = httpx.Response(
        200,
        json={
            "access_token": "access-token",
            "expires_in": "3600",
        },
    )

    token_result = codeagent_auth_module._build_token_result(
        response,
        fallback_refresh_token="fallback-refresh-token",
    )

    assert token_result.refresh_token == "fallback-refresh-token"


def test_build_token_result_requires_access_and_refresh_tokens() -> None:
    response = httpx.Response(200, json={"access_token": "access-token"})

    with pytest.raises(
        CodeAgentOAuthError,
        match="did not include access_token and refresh_token",
    ):
        codeagent_auth_module._build_token_result(
            response,
            fallback_refresh_token=None,
        )


def test_extract_helpers_handle_non_mapping_and_bool_values() -> None:
    assert codeagent_auth_module._extract_str(None, ("token",)) is None
    assert codeagent_auth_module._extract_int(None, ("expires_in",)) is None
    assert (
        codeagent_auth_module._extract_str(
            {"token": " access-token "},
            ("token",),
        )
        == "access-token"
    )
    assert (
        codeagent_auth_module._extract_int(
            {"expires_in": True},
            ("expires_in",),
        )
        is None
    )
    assert (
        codeagent_auth_module._extract_int(
            {"expires_in": 12},
            ("expires_in",),
        )
        == 12
    )
    assert (
        codeagent_auth_module._extract_int(
            {"expires_in": "3600"},
            ("expires_in",),
        )
        == 3600
    )


def test_response_json_returns_none_for_invalid_json() -> None:
    response = httpx.Response(200, content=b"not-json")

    assert codeagent_auth_module._response_json(response) is None


def test_get_codeagent_token_service_returns_default_singleton() -> None:
    assert (
        codeagent_auth_module.get_codeagent_token_service()
        is codeagent_auth_module.get_codeagent_token_service()
    )


@pytest.mark.asyncio
async def test_build_codeagent_openai_client_exposes_custom_auth() -> None:
    http_client = httpx.AsyncClient()
    try:
        client = codeagent_auth_module.build_codeagent_openai_client(
            base_url=DEFAULT_CODEAGENT_BASE_URL,
            auth_config=CodeAgentAuthConfig(refresh_token="refresh-token"),
            default_headers={"X-Test": "1"},
            http_client=http_client,
            connect_timeout_seconds=15.0,
            ssl_verify=None,
            token_service=CodeAgentTokenService(),
        )

        assert isinstance(client, codeagent_auth_module.CodeAgentAsyncOpenAI)
        assert client.auth_headers == {}
        assert client.custom_auth is not None
    finally:
        await http_client.aclose()
