# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Iterator
from typing import cast

import httpx
import pytest

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.net import llm_client
from relay_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS


_SSL_VERIFY_DISABLED = 0
_SSL_VERIFY_REQUIRED = 2


@pytest.fixture(autouse=True)
def clear_llm_http_client_cache() -> Iterator[None]:
    llm_client.clear_llm_http_client_cache()
    yield
    llm_client.clear_llm_http_client_cache()


def _transport_verify_mode(transport: object) -> int:
    pool = getattr(transport, "_pool")
    ssl_context = getattr(pool, "_ssl_context")
    return int(ssl_context.verify_mode)


def _routing_transport(client: httpx.AsyncClient) -> object:
    transport = client._transport
    assert type(transport).__name__ == "AsyncProxyRoutingTransport"
    return transport


def test_build_llm_http_client_builds_direct_client_without_proxy_config() -> None:
    client = llm_client.build_llm_http_client(merged_env={})
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert client.trust_env is False
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED
    assert client.timeout.connect == DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
    assert getattr(transport, "_http_proxy_transport") is None
    assert getattr(transport, "_https_proxy_transport") is None


def test_build_llm_http_client_loads_saved_proxy_settings_when_env_not_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAsyncClient:
        def __init__(self) -> None:
            self.is_closed = False

        async def aclose(self) -> None:
            self.is_closed = True

    captured_kwargs: dict[str, object] = {}

    monkeypatch.setattr(
        llm_client,
        "load_proxy_env_config",
        lambda: ProxyEnvConfig(
            https_proxy="http://alice:secret@proxy.internal:8443",
            no_proxy="localhost,127.0.0.1",
            ssl_verify=False,
        ),
    )
    monkeypatch.setattr(
        llm_client,
        "create_async_http_client",
        lambda **kwargs: captured_kwargs.update(kwargs) or _FakeAsyncClient(),
    )

    client = llm_client.build_llm_http_client()

    assert isinstance(client, _FakeAsyncClient)
    assert captured_kwargs == {
        "merged_env": {
            "HTTPS_PROXY": "http://alice:secret@proxy.internal:8443",
            "https_proxy": "http://alice:secret@proxy.internal:8443",
            "NO_PROXY": "localhost,127.0.0.1",
            "no_proxy": "localhost,127.0.0.1",
        },
        "ssl_verify": False,
        "connect_timeout_seconds": DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    }


def test_build_llm_http_client_uses_requested_connect_timeout() -> None:
    client = llm_client.build_llm_http_client(
        merged_env={},
        connect_timeout_seconds=42.5,
    )

    assert client.timeout.connect == 42.5


def test_build_llm_http_client_builds_proxy_and_no_proxy_mounts() -> None:
    client = llm_client.build_llm_http_client(
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
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED
    assert isinstance(http_proxy_transport, httpx.AsyncHTTPTransport)
    assert isinstance(https_proxy_transport, httpx.AsyncHTTPTransport)
    assert select_transport("http://service.example.net") is http_proxy_transport
    assert select_transport("https://service.example.net") is https_proxy_transport
    assert select_transport("https://localhost") is direct_transport
    assert select_transport("https://example.com") is direct_transport
    assert select_transport("https://127.0.0.1:8443") is direct_transport
    assert select_transport("https://[::1]/") is direct_transport


def test_build_llm_http_client_respects_no_proxy_wildcard() -> None:
    client = llm_client.build_llm_http_client(
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
    client = llm_client.build_llm_http_client(
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
    client = llm_client.build_llm_http_client(merged_env={"SSL_VERIFY": "false"})
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_DISABLED
    assert getattr(transport, "_http_proxy_transport") is None
    assert getattr(transport, "_https_proxy_transport") is None


def test_build_llm_http_client_enables_ssl_verification_when_configured() -> None:
    client = llm_client.build_llm_http_client(merged_env={"SSL_VERIFY": "true"})
    transport = _routing_transport(client)
    direct_transport = getattr(transport, "_direct_transport")

    assert client is not None
    assert _transport_verify_mode(direct_transport) == _SSL_VERIFY_REQUIRED


@pytest.mark.asyncio
async def test_reset_llm_http_client_cache_entry_closes_targeted_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAsyncClient:
        def __init__(self) -> None:
            self.is_closed = False
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            self.is_closed = True

    created_clients: list[_FakeAsyncClient] = []

    def _create_async_http_client(**_: object) -> _FakeAsyncClient:
        client = _FakeAsyncClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        llm_client,
        "create_async_http_client",
        _create_async_http_client,
    )

    first_client = cast(
        _FakeAsyncClient,
        llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
            connect_timeout_seconds=42.5,
            ssl_verify=False,
        ),
    )
    second_client = cast(
        _FakeAsyncClient,
        llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:9443"},
            connect_timeout_seconds=42.5,
            ssl_verify=False,
        ),
    )

    await llm_client.reset_llm_http_client_cache_entry(
        merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
        connect_timeout_seconds=42.5,
        ssl_verify=False,
    )

    assert first_client.close_calls == 1
    assert first_client.is_closed is True
    assert second_client.close_calls == 0

    rebuilt_client = cast(
        _FakeAsyncClient,
        llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
            connect_timeout_seconds=42.5,
            ssl_verify=False,
        ),
    )

    assert rebuilt_client is not first_client
    assert len(created_clients) == 3


@pytest.mark.asyncio
async def test_reset_llm_http_client_cache_entry_isolated_by_cache_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAsyncClient:
        def __init__(self) -> None:
            self.is_closed = False
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            self.is_closed = True

    created_clients: list[_FakeAsyncClient] = []

    def _create_async_http_client(**_: object) -> _FakeAsyncClient:
        client = _FakeAsyncClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        llm_client,
        "create_async_http_client",
        _create_async_http_client,
    )

    run_a_client = cast(
        _FakeAsyncClient,
        llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
            connect_timeout_seconds=42.5,
            cache_scope="run-a",
            ssl_verify=False,
        ),
    )
    run_b_client = cast(
        _FakeAsyncClient,
        llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
            connect_timeout_seconds=42.5,
            cache_scope="run-b",
            ssl_verify=False,
        ),
    )

    await llm_client.reset_llm_http_client_cache_entry(
        merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
        connect_timeout_seconds=42.5,
        cache_scope="run-a",
        ssl_verify=False,
    )

    assert run_a_client.close_calls == 1
    assert run_b_client.close_calls == 0


def test_build_llm_http_client_does_not_evict_scoped_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAsyncClient:
        def __init__(self) -> None:
            self.is_closed = False
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            self.is_closed = True

    created_clients: list[_FakeAsyncClient] = []

    def _create_async_http_client(**_: object) -> _FakeAsyncClient:
        client = _FakeAsyncClient()
        created_clients.append(client)
        return client

    monkeypatch.setattr(
        llm_client,
        "create_async_http_client",
        _create_async_http_client,
    )

    for index in range(40):
        _ = llm_client.build_llm_http_client(
            merged_env={"HTTPS_PROXY": "http://proxy.internal:8443"},
            connect_timeout_seconds=42.5,
            cache_scope=f"run-{index}",
            ssl_verify=False,
        )

    assert len(created_clients) == 40
    assert all(client.close_calls == 0 for client in created_clients)
