# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx

from relay_teams.providers.codeagent_auth import (
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
