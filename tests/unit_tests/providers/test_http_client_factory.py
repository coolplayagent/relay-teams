# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx

from agent_teams.providers import http_client_factory


_SSL_VERIFY_DISABLED = 0
_SSL_VERIFY_REQUIRED = 2


def _transport_verify_mode(transport: object) -> int:
    pool = getattr(transport, "_pool")
    ssl_context = getattr(pool, "_ssl_context")
    return int(ssl_context.verify_mode)


def test_build_llm_http_client_returns_none_without_proxy_config() -> None:
    client = http_client_factory.build_llm_http_client(merged_env={})

    assert client is None


def test_build_llm_http_client_builds_proxy_and_no_proxy_mounts() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "http_proxy": "proxy.internal:8080",
            "no_proxy": "localhost,example.com,127.0.0.1,::1",
        }
    )

    assert client is not None
    assert client.trust_env is False
    assert client.headers["User-Agent"]
    assert _transport_verify_mode(client._transport) == _SSL_VERIFY_REQUIRED

    mounts = {
        pattern.pattern: transport for pattern, transport in client._mounts.items()
    }

    assert isinstance(mounts["http://"], httpx.AsyncHTTPTransport)
    assert isinstance(mounts["https://"], httpx.AsyncHTTPTransport)
    assert mounts["all://localhost"] is None
    assert mounts["all://*example.com"] is None
    assert mounts["all://127.0.0.1"] is None
    assert mounts["all://[::1]"] is None


def test_build_llm_http_client_respects_no_proxy_wildcard() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "HTTP_PROXY": "http://proxy.internal:8080",
            "NO_PROXY": "*",
        }
    )

    assert client is not None
    mounts = {
        pattern.pattern: transport for pattern, transport in client._mounts.items()
    }
    assert mounts == {}


def test_build_llm_http_client_disables_ssl_verification_when_configured() -> None:
    client = http_client_factory.build_llm_http_client(
        merged_env={
            "HTTP_PROXY": "http://proxy.internal:8080",
            "AGENT_TEAMS_LLM_SSL_VERIFY": "false",
        }
    )

    assert client is not None
    assert _transport_verify_mode(client._transport) == _SSL_VERIFY_DISABLED
    mounts = {
        pattern.pattern: transport for pattern, transport in client._mounts.items()
    }
    assert isinstance(mounts["https://"], httpx.AsyncHTTPTransport)
    assert _transport_verify_mode(mounts["https://"]) == _SSL_VERIFY_DISABLED


def test_build_llm_http_client_creates_direct_client_when_only_ssl_verification_is_disabled() -> (
    None
):
    client = http_client_factory.build_llm_http_client(
        merged_env={"AGENT_TEAMS_LLM_SSL_VERIFY": "false"}
    )

    assert client is not None
    assert _transport_verify_mode(client._transport) == _SSL_VERIFY_DISABLED
    assert client._mounts == {}
