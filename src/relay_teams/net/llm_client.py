# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Mapping, Sequence

import httpx

from relay_teams.env.proxy_env import (
    load_proxy_env_config,
    resolve_proxy_env_config,
    resolve_ssl_verify,
)
from relay_teams.net.clients import create_async_http_client
from relay_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS

__all__ = [
    "build_llm_http_client",
    "clear_llm_http_client_cache",
    "clear_llm_http_client_cache_async",
    "reset_llm_http_client_cache_entry",
]

_PROXY_CACHE_KEYS = (
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)
_LLM_HTTP_CLIENT_CACHE_MAXSIZE = 32
type _LlmHttpClientCacheKey = tuple[frozenset[tuple[str, str]], bool, float, str | None]
_SHARED_LLM_HTTP_CLIENT_CACHE: OrderedDict[
    _LlmHttpClientCacheKey, httpx.AsyncClient
] = OrderedDict()
_SCOPED_LLM_HTTP_CLIENT_CACHE: dict[_LlmHttpClientCacheKey, httpx.AsyncClient] = {}


def build_llm_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    cache_scope: str | None = None,
) -> httpx.AsyncClient:
    resolved_env, effective_ssl_verify = _resolve_client_config(
        merged_env=merged_env,
        ssl_verify=ssl_verify,
    )
    cache_key = _build_llm_http_client_cache_key(
        merged_env=resolved_env,
        ssl_verify=effective_ssl_verify,
        connect_timeout_seconds=connect_timeout_seconds,
        cache_scope=cache_scope,
    )
    cache = _select_llm_http_client_cache(cache_scope=cache_scope)
    client = cache.get(cache_key)
    if client is not None and client.is_closed:
        cache.pop(cache_key, None)
        client = None
    if client is None:
        client = create_async_http_client(
            merged_env=dict(cache_key[0]),
            connect_timeout_seconds=connect_timeout_seconds,
            ssl_verify=effective_ssl_verify,
        )
        cache[cache_key] = client
        if cache_scope is None:
            _SHARED_LLM_HTTP_CLIENT_CACHE.move_to_end(cache_key)
            _evict_stale_llm_http_clients()
        return client
    if cache_scope is None:
        _SHARED_LLM_HTTP_CLIENT_CACHE.move_to_end(cache_key)
    return client


def clear_llm_http_client_cache() -> None:
    clients = tuple(_SHARED_LLM_HTTP_CLIENT_CACHE.values()) + tuple(
        _SCOPED_LLM_HTTP_CLIENT_CACHE.values()
    )
    _SHARED_LLM_HTTP_CLIENT_CACHE.clear()
    _SCOPED_LLM_HTTP_CLIENT_CACHE.clear()
    _close_llm_http_clients(clients)


async def clear_llm_http_client_cache_async() -> None:
    clients = tuple(_SHARED_LLM_HTTP_CLIENT_CACHE.values()) + tuple(
        _SCOPED_LLM_HTTP_CLIENT_CACHE.values()
    )
    _SHARED_LLM_HTTP_CLIENT_CACHE.clear()
    _SCOPED_LLM_HTTP_CLIENT_CACHE.clear()
    await _close_llm_http_clients_async(clients)


async def reset_llm_http_client_cache_entry(
    *,
    merged_env: Mapping[str, str] | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float,
    cache_scope: str | None = None,
) -> None:
    resolved_env, effective_ssl_verify = _resolve_client_config(
        merged_env=merged_env,
        ssl_verify=ssl_verify,
    )
    cache_key = _build_llm_http_client_cache_key(
        merged_env=resolved_env,
        ssl_verify=effective_ssl_verify,
        connect_timeout_seconds=connect_timeout_seconds,
        cache_scope=cache_scope,
    )
    cache = _select_llm_http_client_cache(cache_scope=cache_scope)
    client = cache.pop(cache_key, None)
    if client is None or client.is_closed:
        return
    await client.aclose()


def _proxy_cache_key(env_values: Mapping[str, str]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (key, value) for key, value in env_values.items() if key in _PROXY_CACHE_KEYS
    )


def _build_llm_http_client_cache_key(
    *,
    merged_env: Mapping[str, str],
    ssl_verify: bool,
    connect_timeout_seconds: float,
    cache_scope: str | None,
) -> _LlmHttpClientCacheKey:
    return (
        _proxy_cache_key(merged_env),
        ssl_verify,
        connect_timeout_seconds,
        cache_scope,
    )


def _resolve_client_config(
    *,
    merged_env: Mapping[str, str] | None,
    ssl_verify: bool | None,
) -> tuple[Mapping[str, str], bool]:
    if merged_env is None:
        proxy_config = load_proxy_env_config()
        resolved_env = proxy_config.normalized_env()
        effective_ssl_verify = resolve_ssl_verify(
            proxy_config=proxy_config,
            explicit_ssl_verify=ssl_verify,
        )
        return resolved_env, effective_ssl_verify
    effective_ssl_verify = _resolve_effective_ssl_verify(
        merged_env=merged_env,
        ssl_verify=ssl_verify,
    )
    return merged_env, effective_ssl_verify


def _evict_stale_llm_http_clients() -> None:
    stale_clients: list[httpx.AsyncClient] = []
    while len(_SHARED_LLM_HTTP_CLIENT_CACHE) > _LLM_HTTP_CLIENT_CACHE_MAXSIZE:
        _, stale_client = _SHARED_LLM_HTTP_CLIENT_CACHE.popitem(last=False)
        stale_clients.append(stale_client)
    _close_llm_http_clients(stale_clients)


def _select_llm_http_client_cache(
    *,
    cache_scope: str | None,
) -> (
    OrderedDict[_LlmHttpClientCacheKey, httpx.AsyncClient]
    | dict[_LlmHttpClientCacheKey, httpx.AsyncClient]
):
    if cache_scope is None:
        return _SHARED_LLM_HTTP_CLIENT_CACHE
    return _SCOPED_LLM_HTTP_CLIENT_CACHE


def _close_llm_http_clients(clients: Sequence[httpx.AsyncClient]) -> None:
    open_clients = [client for client in clients if not client.is_closed]
    if not open_clients:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_close_llm_http_clients_async(open_clients))
        return
    loop.create_task(_close_llm_http_clients_async(open_clients))


async def _close_llm_http_clients_async(
    clients: Sequence[httpx.AsyncClient],
) -> None:
    for client in clients:
        if client.is_closed:
            continue
        await client.aclose()


def _resolve_effective_ssl_verify(
    *,
    merged_env: Mapping[str, str],
    ssl_verify: bool | None,
) -> bool:
    return resolve_ssl_verify(
        proxy_config=resolve_proxy_env_config(merged_env),
        explicit_ssl_verify=ssl_verify,
    )
