# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping

import httpx
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, get_user_agent

from agent_teams.env.proxy_env import (
    ProxyEnvConfig,
    proxy_applies_to_url,
    resolve_ssl_verify,
    resolve_proxy_env_config,
)
from agent_teams.env.runtime_env import load_merged_env_vars
from agent_teams.providers.model_config import DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS


def create_proxy_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    proxy_config: ProxyEnvConfig | None = None,
    ssl_verify: bool | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
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
        transport=_SyncProxyRoutingTransport(
            proxy_config=resolved_proxy_config,
            ssl_verify=resolved_ssl_verify,
        ),
        follow_redirects=follow_redirects,
    )


def create_proxy_async_http_client(
    *,
    merged_env: Mapping[str, str] | None = None,
    proxy_config: ProxyEnvConfig | None = None,
    ssl_verify: bool | None = None,
    timeout_seconds: float = DEFAULT_HTTP_TIMEOUT,
    connect_timeout_seconds: float = DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS,
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
        transport=_AsyncProxyRoutingTransport(
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
    return _resolve_proxy_env_config(resolved_env)


def _resolve_proxy_env_config(
    env_values: Mapping[str, str],
) -> ProxyEnvConfig:
    return resolve_proxy_env_config(env_values)


class _SyncProxyRoutingTransport(httpx.BaseTransport):
    def __init__(
        self,
        proxy_config: ProxyEnvConfig,
        *,
        ssl_verify: bool,
    ) -> None:
        self._proxy_config = proxy_config
        self._ssl_verify = ssl_verify
        self._direct_transport = httpx.HTTPTransport(
            trust_env=False,
            verify=ssl_verify,
            retries=0,
        )
        self._http_proxy_transport = _build_sync_proxy_transport(
            proxy_config.http_proxy or proxy_config.all_proxy,
            ssl_verify=ssl_verify,
        )
        self._https_proxy_transport = _build_sync_proxy_transport(
            proxy_config.https_proxy
            or proxy_config.http_proxy
            or proxy_config.all_proxy,
            ssl_verify=ssl_verify,
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._select_transport(str(request.url))
        return transport.handle_request(request)

    def close(self) -> None:
        self._direct_transport.close()
        if self._http_proxy_transport is not None:
            self._http_proxy_transport.close()
        if (
            self._https_proxy_transport is not None
            and self._https_proxy_transport is not self._http_proxy_transport
        ):
            self._https_proxy_transport.close()

    def _select_transport(self, url: str) -> httpx.BaseTransport:
        if not proxy_applies_to_url(url, self._proxy_config):
            return self._direct_transport
        if url.startswith("http://") and self._http_proxy_transport is not None:
            return self._http_proxy_transport
        if url.startswith("https://") and self._https_proxy_transport is not None:
            return self._https_proxy_transport
        return self._direct_transport


class _AsyncProxyRoutingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        proxy_config: ProxyEnvConfig,
        *,
        ssl_verify: bool,
    ) -> None:
        self._proxy_config = proxy_config
        self._ssl_verify = ssl_verify
        self._direct_transport = httpx.AsyncHTTPTransport(
            trust_env=False,
            verify=ssl_verify,
            retries=0,
        )
        self._http_proxy_transport = _build_async_proxy_transport(
            proxy_config.http_proxy or proxy_config.all_proxy,
            ssl_verify=ssl_verify,
        )
        self._https_proxy_transport = _build_async_proxy_transport(
            proxy_config.https_proxy
            or proxy_config.http_proxy
            or proxy_config.all_proxy,
            ssl_verify=ssl_verify,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        transport = self._select_transport(str(request.url))
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self._direct_transport.aclose()
        if self._http_proxy_transport is not None:
            await self._http_proxy_transport.aclose()
        if (
            self._https_proxy_transport is not None
            and self._https_proxy_transport is not self._http_proxy_transport
        ):
            await self._https_proxy_transport.aclose()

    def _select_transport(self, url: str) -> httpx.AsyncBaseTransport:
        if not proxy_applies_to_url(url, self._proxy_config):
            return self._direct_transport
        if url.startswith("http://") and self._http_proxy_transport is not None:
            return self._http_proxy_transport
        if url.startswith("https://") and self._https_proxy_transport is not None:
            return self._https_proxy_transport
        return self._direct_transport


def _build_sync_proxy_transport(
    proxy_url: str | None,
    *,
    ssl_verify: bool,
) -> httpx.HTTPTransport | None:
    if proxy_url is None:
        return None
    normalized_proxy_url = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    return httpx.HTTPTransport(
        proxy=normalized_proxy_url,
        trust_env=False,
        verify=ssl_verify,
    )


def _build_async_proxy_transport(
    proxy_url: str | None,
    *,
    ssl_verify: bool,
) -> httpx.AsyncHTTPTransport | None:
    if proxy_url is None:
        return None
    normalized_proxy_url = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    return httpx.AsyncHTTPTransport(
        proxy=normalized_proxy_url,
        trust_env=False,
        verify=ssl_verify,
    )
