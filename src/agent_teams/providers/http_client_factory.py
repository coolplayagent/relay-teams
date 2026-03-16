# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

import httpx

from agent_teams.env.proxy_env import (
    ProxyEnvConfig,
    load_proxy_env_config,
    resolve_proxy_env_config,
    resolve_ssl_verify,
)
from agent_teams.env.proxy_http_client import create_proxy_async_http_client
from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS

__all__ = ["ProxyEnvConfig", "build_llm_http_client", "clear_llm_http_client_cache"]
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


def build_llm_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    ssl_verify: bool | None = None,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
) -> httpx.AsyncClient:
    if merged_env is None:
        proxy_config = load_proxy_env_config()
        resolved_env = proxy_config.normalized_env()
        effective_ssl_verify = resolve_ssl_verify(
            proxy_config=proxy_config,
            explicit_ssl_verify=ssl_verify,
        )
    else:
        resolved_env = merged_env
        effective_ssl_verify = _resolve_effective_ssl_verify(
            merged_env=resolved_env,
            ssl_verify=ssl_verify,
        )
    client = _cached_llm_http_client(
        merged_env=_proxy_cache_key(resolved_env),
        ssl_verify=effective_ssl_verify,
        connect_timeout_seconds=connect_timeout_seconds,
    )
    if client.is_closed:
        _cached_llm_http_client.cache_clear()
        client = _cached_llm_http_client(
            merged_env=_proxy_cache_key(resolved_env),
            ssl_verify=effective_ssl_verify,
            connect_timeout_seconds=connect_timeout_seconds,
        )
    return client


def clear_llm_http_client_cache() -> None:
    _cached_llm_http_client.cache_clear()


@lru_cache(maxsize=32)
def _cached_llm_http_client(
    *,
    merged_env: frozenset[tuple[str, str]],
    ssl_verify: bool,
    connect_timeout_seconds: float,
) -> httpx.AsyncClient:
    return create_proxy_async_http_client(
        merged_env=dict(merged_env),
        ssl_verify=ssl_verify,
        connect_timeout_seconds=connect_timeout_seconds,
    )


def _proxy_cache_key(env_values: Mapping[str, str]) -> frozenset[tuple[str, str]]:
    return frozenset(
        (key, value) for key, value in env_values.items() if key in _PROXY_CACHE_KEYS
    )


def _resolve_effective_ssl_verify(
    *,
    merged_env: Mapping[str, str],
    ssl_verify: bool | None,
) -> bool:
    return resolve_ssl_verify(
        proxy_config=resolve_proxy_env_config(merged_env),
        explicit_ssl_verify=ssl_verify,
    )
