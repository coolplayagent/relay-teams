# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from relay_teams.env.proxy_env import ProxyEnvConfig, proxy_applies_to_url


class AsyncRequestLimitLease(Protocol):
    @staticmethod
    def release() -> None:
        """Release an acquired request slot."""
        raise NotImplementedError  # pragma: no cover


class AsyncRequestLimiter(Protocol):
    @staticmethod
    async def acquire(url: str) -> AsyncRequestLimitLease:
        """Acquire a request slot for the target URL."""
        raise NotImplementedError  # pragma: no cover


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
        request_limiter: AsyncRequestLimiter | None = None,
    ) -> None:
        self._proxy_config = proxy_config
        self._request_limiter = request_limiter
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
        if self._request_limiter is None:
            return await transport.handle_async_request(request)
        lease = await self._request_limiter.acquire(str(request.url))
        try:
            response = await transport.handle_async_request(request)
        except BaseException:
            lease.release()
            raise
        stream = response.stream
        if not isinstance(stream, httpx.AsyncByteStream):
            lease.release()
            raise TypeError(
                "Async proxy transport received a non-async response stream"
            )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_ReleasingAsyncByteStream(stream, lease),
            request=request,
            extensions=response.extensions,
            default_encoding=response.default_encoding,
        )

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


class _ReleasingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(
        self,
        stream: httpx.AsyncByteStream,
        lease: AsyncRequestLimitLease,
    ) -> None:
        self._stream = stream
        self._lease = lease
        self._released = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._stream:
                yield chunk
        finally:
            self._release()

    async def aclose(self) -> None:
        try:
            await self._stream.aclose()
        finally:
            self._release()

    def _release(self) -> None:
        if self._released:
            return
        self._released = True
        self._lease.release()
