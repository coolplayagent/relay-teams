# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx

from relay_teams.net.clients import create_sync_http_client

_SSL_VERIFY_DISABLED = 0
_SSL_VERIFY_REQUIRED = 2


def _transport_verify_mode(transport: object) -> int:
    pool = getattr(transport, "_pool")
    ssl_context = getattr(pool, "_ssl_context")
    return int(ssl_context.verify_mode)


def test_create_sync_http_client_routes_requests_with_runtime_proxy_rules() -> None:
    with create_sync_http_client(
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
    assert isinstance(https_proxy_transport, httpx.HTTPTransport)
    assert isinstance(direct_transport, httpx.HTTPTransport)
    assert select_transport("https://service.example.net") is https_proxy_transport
    assert select_transport("https://example.com") is direct_transport
    assert select_transport("https://127.0.0.1:8443") is direct_transport
    assert select_transport("https://printer") is direct_transport
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED


def test_create_sync_http_client_enables_ssl_verification_when_configured() -> None:
    with create_sync_http_client(merged_env={"SSL_VERIFY": "true"}) as client:
        verify_mode = _transport_verify_mode(
            getattr(client._transport, "_direct_transport")
        )

    assert verify_mode == _SSL_VERIFY_REQUIRED


def test_create_sync_http_client_disables_ssl_verification_when_configured() -> None:
    with create_sync_http_client(merged_env={"SSL_VERIFY": "false"}) as client:
        verify_mode = _transport_verify_mode(
            getattr(client._transport, "_direct_transport")
        )

    assert verify_mode == _SSL_VERIFY_DISABLED
