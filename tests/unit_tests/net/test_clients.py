# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import httpx
import pytest

import relay_teams.net.clients as clients_module
from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.net.clients import create_async_http_client
from relay_teams.net.proxy_transports import AsyncRequestLimiter

_SSL_VERIFY_DISABLED = 0
_SSL_VERIFY_REQUIRED = 2


def _transport_verify_mode(transport: object) -> int:
    pool = getattr(transport, "_pool")
    ssl_context = getattr(pool, "_ssl_context")
    return int(ssl_context.verify_mode)


@pytest.mark.asyncio
async def test_create_async_http_client_routes_requests_with_runtime_proxy_rules() -> (
    None
):
    async with create_async_http_client(
        merged_env={
            "HTTPS_PROXY": "http://proxy.example:8080",
            "NO_PROXY": "localhost;127.*;example.com;<local>",
        }
    ) as client:
        transport = client._transport
        select_transport = getattr(transport, "_select_transport")
        direct_transport = getattr(transport, "_direct_transport")
        https_proxy_transport = getattr(transport, "_https_proxy_transport")

    assert client.trust_env is False
    assert isinstance(https_proxy_transport, httpx.AsyncHTTPTransport)
    assert isinstance(direct_transport, httpx.AsyncHTTPTransport)
    assert select_transport("https://service.example.net") is https_proxy_transport
    assert select_transport("https://example.com") is direct_transport
    assert select_transport("https://127.0.0.1:8443") is direct_transport
    assert select_transport("https://printer") is direct_transport
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED


@pytest.mark.asyncio
async def test_create_async_http_client_enables_ssl_verification_when_configured() -> (
    None
):
    async with create_async_http_client(merged_env={"SSL_VERIFY": "true"}) as client:
        verify_mode = _transport_verify_mode(
            getattr(client._transport, "_direct_transport")
        )

    assert verify_mode == _SSL_VERIFY_REQUIRED


@pytest.mark.asyncio
async def test_create_async_http_client_disables_ssl_verification_when_configured() -> (
    None
):
    async with create_async_http_client(merged_env={"SSL_VERIFY": "false"}) as client:
        verify_mode = _transport_verify_mode(
            getattr(client._transport, "_direct_transport")
        )

    assert verify_mode == _SSL_VERIFY_DISABLED


def test_create_runtime_async_http_client_uses_hydrated_proxy_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_config = ProxyEnvConfig(https_proxy="http://user:secret@proxy.example:8443")
    captured_proxy_configs: list[ProxyEnvConfig | None] = []
    captured_limiters: list[AsyncRequestLimiter | None] = []

    def fake_create_async_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        proxy_config: ProxyEnvConfig | None = None,
        ssl_verify: bool | None = None,
        timeout_seconds: float = 0.0,
        connect_timeout_seconds: float = 0.0,
        follow_redirects: bool = False,
        request_limiter: AsyncRequestLimiter | None = None,
    ) -> httpx.AsyncClient:
        _ = (
            merged_env,
            ssl_verify,
            timeout_seconds,
            connect_timeout_seconds,
            follow_redirects,
        )
        captured_proxy_configs.append(proxy_config)
        captured_limiters.append(request_limiter)
        return cast(httpx.AsyncClient, object())

    monkeypatch.setattr(
        clients_module,
        "load_proxy_env_config",
        lambda: proxy_config,
    )
    monkeypatch.setattr(
        clients_module,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    _ = clients_module.create_runtime_async_http_client()

    assert captured_proxy_configs == [proxy_config]
    assert captured_limiters == [None]


def test_create_runtime_async_http_client_forwards_all_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: list[dict[str, object]] = []

    def fake_create_async_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        proxy_config: ProxyEnvConfig | None = None,
        ssl_verify: bool | None = None,
        timeout_seconds: float = 0.0,
        connect_timeout_seconds: float = 0.0,
        follow_redirects: bool = False,
        request_limiter: AsyncRequestLimiter | None = None,
    ) -> httpx.AsyncClient:
        captured_kwargs.append(
            {
                "proxy_config": proxy_config,
                "ssl_verify": ssl_verify,
                "timeout_seconds": timeout_seconds,
                "connect_timeout_seconds": connect_timeout_seconds,
                "follow_redirects": follow_redirects,
                "request_limiter": request_limiter,
            }
        )
        return cast(httpx.AsyncClient, object())

    monkeypatch.setattr(
        clients_module,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(),
    )
    monkeypatch.setattr(
        clients_module,
        "create_async_http_client",
        fake_create_async_http_client,
    )

    sentinel_limiter: AsyncRequestLimiter | None = None

    _ = clients_module.create_runtime_async_http_client(
        ssl_verify=True,
        timeout_seconds=99.0,
        connect_timeout_seconds=5.0,
        follow_redirects=True,
        request_limiter=sentinel_limiter,
    )

    assert len(captured_kwargs) == 1
    kw = captured_kwargs[0]
    assert kw["ssl_verify"] is True
    assert kw["timeout_seconds"] == 99.0
    assert kw["connect_timeout_seconds"] == 5.0
    assert kw["follow_redirects"] is True
    assert kw["request_limiter"] is sentinel_limiter
