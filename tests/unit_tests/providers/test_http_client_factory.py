# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from agent_teams.providers import http_client_factory
from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS


_SSL_VERIFY_DISABLED = 0
_SSL_VERIFY_REQUIRED = 2


@pytest.fixture(autouse=True)
def clear_llm_http_client_cache() -> Iterator[None]:
    http_client_factory.clear_llm_http_client_cache()
    yield
    http_client_factory.clear_llm_http_client_cache()


def _transport_verify_mode(transport: object) -> int:
    pool = getattr(transport, "_pool")
    ssl_context = getattr(pool, "_ssl_context")
    return int(ssl_context.verify_mode)


def _routing_transport(client: httpx.AsyncClient) -> object:
    transport = client._transport
    assert type(transport).__name__ == "_AsyncProxyRoutingTransport"
    return transport


def test_build_llm_http_client_builds_direct_client_without_proxy_config() -> None:
    client = http_client_factory.build_llm_http_client(merged_env={})
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert client.trust_env is False
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_REQUIRED
    assert client.timeout.connect == DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS
    assert getattr(transport, "_http_proxy_transport") is None
    assert getattr(transport, "_https_proxy_transport") is None


def test_build_llm_http_client_uses_requested_connect_timeout() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={},
        connect_timeout_seconds=42.5,
    )

    assert client.timeout.connect == 42.5


def test_build_llm_http_client_builds_proxy_and_no_proxy_mounts() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "http_proxy": "proxy.internal:8080",
            "no_proxy": "localhost,example.com,127.0.0.1,::1",
        }
    )
    transport = _routing_transport(client)
    select_transport = getattr(transport, "_select_transport")
    direct_transport = getattr(transport, "_direct_transport")
    http_proxy_transport = getattr(transport, "_http_proxy_transport")
    https_proxy_transport = getattr(transport, "_https_proxy_transport")

    assert client is not None
    assert client.trust_env is False
    assert client.headers["User-Agent"]
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_REQUIRED
    assert isinstance(http_proxy_transport, httpx.AsyncHTTPTransport)
    assert isinstance(https_proxy_transport, httpx.AsyncHTTPTransport)
    assert select_transport("http://service.example.net") is http_proxy_transport
    assert select_transport("https://service.example.net") is https_proxy_transport
    assert select_transport("https://localhost") is direct_transport
    assert select_transport("https://example.com") is direct_transport
    assert select_transport("https://127.0.0.1:8443") is direct_transport
    assert select_transport("https://[::1]/") is direct_transport


def test_build_llm_http_client_respects_no_proxy_wildcard() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "HTTP_PROXY": "http://proxy.internal:8080",
            "NO_PROXY": "*",
        }
    )
    transport = _routing_transport(client)
    select_transport = getattr(transport, "_select_transport")
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert select_transport("http://service.example.net") is direct_transport
    assert select_transport("https://service.example.net") is direct_transport


def test_build_llm_http_client_disables_ssl_verification_when_configured() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "HTTP_PROXY": "http://proxy.internal:8080",
            "SSL_VERIFY": "false",
        }
    )
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")
    https_proxy_transport = getattr(transport, "_https_proxy_transport")

    assert client is not None
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED
    assert isinstance(https_proxy_transport, httpx.AsyncHTTPTransport)
    assert _transport_verify_mode(https_proxy_transport) == _SSL_VERIFY_DISABLED


def test_build_llm_http_client_creates_direct_client_when_only_ssl_verification_is_disabled() -> (
    None
):
    client = http_client_factory.build_llm_http_client(
        merged_env={"SSL_VERIFY": "false"}
    )
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED
    assert getattr(transport, "_http_proxy_transport") is None
    assert getattr(transport, "_https_proxy_transport") is None
