# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from relay_teams.providers.maas_auth import (
    MaaSTokenService,
    build_maas_openai_client,
)
from relay_teams.providers.model_config import MaaSAuthConfig


class _FakeSyncHttpClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.calls = 0

    def __enter__(self) -> _FakeSyncHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, str],
    ) -> httpx.Response:
        _ = url
        _ = headers
        _ = json
        self.calls += 1
        return self._response


def test_build_maas_openai_client_disables_sdk_retries() -> None:
    http_client = httpx.AsyncClient()
    try:
        client = build_maas_openai_client(
            base_url="https://maas.example/api/v2",
            auth_config=MaaSAuthConfig(
                username="relay-user",
                password="relay-password",
            ),
            default_headers=None,
            http_client=http_client,
            connect_timeout_seconds=15,
            ssl_verify=None,
        )
        assert client.max_retries == 0
    finally:
        asyncio.run(http_client.aclose())


def test_get_auth_context_sync_extracts_department_from_direct_field(
    monkeypatch,
) -> None:
    client = _FakeSyncHttpClient(
        httpx.Response(
            200,
            json={
                "cloudDragonTokens": {"authToken": "maas-token"},
                "userInfo": {"hwDepartName": "Direct Department"},
            },
        )
    )
    service = MaaSTokenService()

    monkeypatch.setattr(
        "relay_teams.providers.maas_auth.create_sync_http_client",
        lambda **_kwargs: client,
    )

    auth_context = service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )

    assert auth_context.token == "maas-token"
    assert auth_context.department == "Direct Department"
    assert client.calls == 1


def test_get_auth_context_sync_falls_back_to_department_segments(monkeypatch) -> None:
    client = _FakeSyncHttpClient(
        httpx.Response(
            200,
            json={
                "cloudDragonTokens": {"authToken": "maas-token"},
                "userInfo": {
                    "hwDepartName1": "Level1",
                    "hwDepartName2": "Level2",
                    "hwDepartName4": "Level4",
                },
            },
        )
    )
    service = MaaSTokenService()

    monkeypatch.setattr(
        "relay_teams.providers.maas_auth.create_sync_http_client",
        lambda **_kwargs: client,
    )

    auth_context = service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )

    assert auth_context.department == "Level1/Level2/Level4"
    assert client.calls == 1


def test_get_auth_context_sync_refreshes_one_hour_before_expiry(monkeypatch) -> None:
    client = _FakeSyncHttpClient(
        httpx.Response(
            200,
            json={
                "cloudDragonTokens": {"authToken": "maas-token"},
                "userInfo": {"hwDepartName": "Direct Department"},
            },
        )
    )
    service = MaaSTokenService()

    monkeypatch.setattr(
        "relay_teams.providers.maas_auth.create_sync_http_client",
        lambda **_kwargs: client,
    )

    cache_key = service._cache_key(
        MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        )
    )
    service._tokens[cache_key] = service._login_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )
    assert client.calls == 1

    service._tokens[cache_key].expires_at = datetime.now(UTC) + timedelta(minutes=59)
    service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )
    assert client.calls == 2

    service._tokens[cache_key].expires_at = datetime.now(UTC) + timedelta(minutes=61)
    service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )
    assert client.calls == 2


def test_get_auth_context_sync_reuses_cached_department(monkeypatch) -> None:
    client = _FakeSyncHttpClient(
        httpx.Response(
            200,
            json={
                "cloudDragonTokens": {"authToken": "maas-token"},
                "userInfo": {"hwDepartName": "Direct Department"},
            },
        )
    )
    service = MaaSTokenService()

    monkeypatch.setattr(
        "relay_teams.providers.maas_auth.create_sync_http_client",
        lambda **_kwargs: client,
    )

    first = service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )
    second = service.get_auth_context_sync(
        auth_config=MaaSAuthConfig(
            username="relay-user",
            password="relay-password",
        ),
        ssl_verify=None,
        connect_timeout_seconds=15,
    )

    assert first == second
    assert second.department == "Direct Department"
    assert client.calls == 1
