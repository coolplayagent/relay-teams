# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from agent_teams.env.proxy_env import (
    ProxyEnvConfig,
    resolve_proxy_env_config,
    resolve_ssl_verify,
)
from agent_teams.env.runtime_env import load_merged_env_vars
from agent_teams.net.constants import DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS
from agent_teams.net.proxy_transports import (
    AsyncProxyRoutingTransport,
    SyncProxyRoutingTransport,
)


def create_sync_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    proxy_config: ProxyEnvConfig | None = None,
    ssl_verify: bool | None = None,
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


def create_async_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    proxy_config: ProxyEnvConfig | None = None,
    ssl_verify: bool | None = None,
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
    merged_env: Mapping[str, str] | None,
    proxy_config: ProxyEnvConfig | None,
) -> ProxyEnvConfig:
    if proxy_config is not None:
        return proxy_config
    resolved_env = load_merged_env_vars() if merged_env is None else merged_env
    return resolve_proxy_env_config(resolved_env)
