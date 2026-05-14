# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
from importlib import metadata

import httpx

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
    AsyncRequestLimiter,
    AsyncProxyRoutingTransport,
)

DEFAULT_HTTP_TIMEOUT = 600
_PYDANTIC_AI_PACKAGE_NAME = "pydantic-ai"


def get_user_agent() -> str:
    try:
        version = metadata.version(_PYDANTIC_AI_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        version = "unknown"
    return f"pydantic-ai/{version}"


def create_async_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    proxy_config: ProxyEnvConfig | None = None,
    headers: Mapping[str, str] | None = None,
    ssl_verify: bool | None = None,
    timeout: httpx.Timeout | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    follow_redirects: bool = False,
    request_limiter: AsyncRequestLimiter | None = None,
) -> httpx.AsyncClient:
    resolved_proxy_config = _resolve_proxy_config(
        merged_env=merged_env,
        proxy_config=proxy_config,
    )
    resolved_ssl_verify = resolve_ssl_verify(
        proxy_config=resolved_proxy_config,
        explicit_ssl_verify=ssl_verify,
    )
    resolved_headers = _resolve_headers(headers)
    return httpx.AsyncClient(
        timeout=(
            timeout
            if timeout is not None
            else httpx.Timeout(
                timeout=timeout_seconds,
                connect=connect_timeout_seconds,
            )
        ),
        headers=resolved_headers,
        trust_env=False,
        verify=resolved_ssl_verify,
        transport=AsyncProxyRoutingTransport(
            proxy_config=resolved_proxy_config,
            ssl_verify=resolved_ssl_verify,
            request_limiter=request_limiter,
        ),
        follow_redirects=follow_redirects,
    )


def create_runtime_async_http_client(
    *,
    ssl_verify: bool | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_HTTP_CONNECT_TIMEOUT_SECONDS,
    follow_redirects: bool = False,
    request_limiter: AsyncRequestLimiter | None = None,
) -> httpx.AsyncClient:
    return create_async_http_client(
        proxy_config=load_proxy_env_config(),
        ssl_verify=ssl_verify,
        timeout_seconds=timeout_seconds,
        connect_timeout_seconds=connect_timeout_seconds,
        follow_redirects=follow_redirects,
        request_limiter=request_limiter,
    )


def _resolve_proxy_config(
    *,
    merged_env: Mapping[str, str] | None,
    proxy_config: ProxyEnvConfig | None,
) -> ProxyEnvConfig:
    if proxy_config is not None:
        return proxy_config
    base_env = load_merged_env_vars() if merged_env is None else merged_env
    resolved_env = merge_tool_hook_runtime_env(base_env)
    if resolved_env is None:
        return resolve_proxy_env_config(base_env)
    return resolve_proxy_env_config(resolved_env)


def _resolve_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    resolved_headers = {"User-Agent": get_user_agent()}
    if headers is not None:
        resolved_headers.update(dict(headers))
    return resolved_headers
