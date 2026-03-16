# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx

from agent_teams.env.proxy_env import ProxyEnvConfig, proxy_applies_to_url


class SyncProxyRoutingTransport(httpx.BaseTransport):
    def __init__(
        self,
        proxy_config: ProxyEnvConfig,
        *,
        ssl_verify: bool,
    ) -> None:
        self._proxy_config = proxy_config
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


class AsyncProxyRoutingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        proxy_config: ProxyEnvConfig,
        *,
        ssl_verify: bool,
    ) -> None:
        self._proxy_config = proxy_config
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
