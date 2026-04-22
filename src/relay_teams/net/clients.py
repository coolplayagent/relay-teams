# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from relay_teams.env.proxy_env import (
    ProxyEnvConfig,
    load_proxy_env_config,
    resolve_proxy_env_config,
    resolve_ssl_verify,
)
from relay_teams.env.runtime_env import load_merged_env_vars
from relay_teams.env.hook_runtime_env import merge_tool_hook_runtime_env
from relay_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
from relay_teams.net.proxy_transports import (
    AsyncProxyRoutingTransport,
    SyncProxyRoutingTransport,
)


def create_sync_http_client(
    *,
    merged_env: Optional[Mapping[str, str]] = None,
    proxy_config: Optional[ProxyEnvConfig] = None,
    ssl_verify: Optional[bool] = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    follow_redirects: bool = False,
) -> httpx.Client:
    resolved_proxy_config = _resolve_proxy_config(
        merged_env=merged_env,
        proxy_config=proxy_config,
    )
    resolved_ssl_verify = resolve_ssl_verify(
        proxy_config=resolved_proxy_config,
        explicit_ssl_verify=ssl_verify,
    )
    return httpx.Client(
        timeout=httpx.Timeout(timeout=timeout_seconds, connect=connect_timeout_seconds),
        headers={"User-Agent": get_user_agent()},
        trust_env=False,
        verify=resolved_ssl_verify,
        transport=SyncProxyRoutingTransport(
            proxy_config=resolved_proxy_config,
            ssl_verify=resolved_ssl_verify,
        ),
        follow_redirects=follow_redirects,
    )


def create_runtime_sync_http_client(
    *,
    ssl_verify: Optional[bool] = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    follow_redirects: bool = False,
) -> httpx.Client:
    return create_sync_http_client(
        proxy_config=load_proxy_env_config(),
        ssl_verify=ssl_verify,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        follow_redirects=follow_redirects,
    )


def create_async_http_client(
    *,
    merged_env: Optional[Mapping[str, str]] = None,
    proxy_config: Optional[ProxyEnvConfig] = None,
    ssl_verify: Optional[bool] = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    follow_redirects: bool = False,
) -> httpx.AsyncClient:
    resolved_proxy_config = _resolve_proxy_config(
        merged_env=merged_env,
        proxy_config=proxy_config,
    )
    resolved_ssl_verify = resolve_ssl_verify(
        proxy_config=resolved_proxy_config,
        explicit_ssl_verify=ssl_verify,
    )
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout=timeout_seconds, connect=connect_timeout_seconds),
        headers={"User-Agent": get_user_agent()},
        trust_env=False,
        verify=resolved_ssl_verify,
        transport=AsyncProxyRoutingTransport(
            proxy_config=resolved_proxy_config,
            ssl_verify=resolved_ssl_verify,
        ),
        follow_redirects=follow_redirects,
    )


def _resolve_proxy_config(
    *,
    merged_env: Optional[Mapping[str, str]],
    proxy_config: Optional[ProxyEnvConfig],
) -> ProxyEnvConfig:
    if proxy_config is not None:
        return proxy_config
    base_env = load_merged_env_vars() if merged_env is None else merged_env
    resolved_env = merge_tool_hook_runtime_env(base_env)
    if resolved_env is None:
        return resolve_proxy_env_config(base_env)
    return resolve_proxy_env_config(resolved_env)
