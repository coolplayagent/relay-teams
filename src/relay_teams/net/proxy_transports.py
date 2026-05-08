# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json
from typing import Protocol

import httpx

from relay_teams.env.proxy_env import ProxyEnvConfig, proxy_applies_to_url

_TERMINAL_SSE_SCAN_BUFFER_BYTES = 64 * 1024


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
        self._terminal_scan_buffer = b""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self._stream:
                if self._chunk_has_terminal_sse_marker(chunk):
                    self._release()
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

    def _chunk_has_terminal_sse_marker(self, chunk: bytes) -> bool:
        scan_target = self._terminal_scan_buffer + chunk
        self._terminal_scan_buffer = scan_target[-_TERMINAL_SSE_SCAN_BUFFER_BYTES:]
        return _chunk_has_terminal_sse_marker(scan_target)


def _chunk_has_terminal_sse_marker(chunk: bytes) -> bool:
    decoded = chunk.decode("utf-8", errors="ignore")
    for payload_text in _iter_sse_data_payloads(decoded):
        if payload_text.upper() == "[DONE]":
            return True
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if _payload_has_terminal_finish_reason(payload):
            return True
        if _payload_has_complete_tool_call_delta(payload):
            return True
    return False


def _chunk_has_complete_tool_call_delta(chunk: bytes) -> bool:
    decoded = chunk.decode("utf-8", errors="ignore")
    for payload_text in _iter_sse_data_payloads(decoded):
        if payload_text.upper() == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if _payload_has_complete_tool_call_delta(payload):
            return True
    return False


def _iter_sse_data_payloads(decoded: str) -> Iterator[str]:
    for line in decoded.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload_text = stripped[5:].strip()
        if payload_text:
            yield payload_text


def _payload_has_terminal_finish_reason(payload: object) -> bool:
    payload_object = _object_dict(payload)
    if payload_object is None:
        return False
    choices = payload_object.get("choices")
    if not isinstance(choices, list):
        return False
    for raw_choice in choices:
        choice = _object_dict(raw_choice)
        if choice is None:
            continue
        if choice.get("finish_reason") is not None:
            return True
    return False


def _payload_has_complete_tool_call_delta(payload: object) -> bool:
    payload_object = _object_dict(payload)
    if payload_object is None:
        return False
    choices = payload_object.get("choices")
    if not isinstance(choices, list):
        return False
    for raw_choice in choices:
        choice = _object_dict(raw_choice)
        if choice is None:
            continue
        delta = _object_dict(choice.get("delta"))
        if delta is None:
            continue
        tool_calls = delta.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            continue
        if _tool_call_deltas_have_complete_arguments(tool_calls):
            return True
    return False


def _tool_call_deltas_have_complete_arguments(tool_calls: list[object]) -> bool:
    saw_tool_call = False
    for raw_tool_call in tool_calls:
        tool_call = _object_dict(raw_tool_call)
        if tool_call is None:
            return False
        saw_tool_call = True
        function_payload = _object_dict(tool_call.get("function"))
        if function_payload is None:
            return False
        arguments = function_payload.get("arguments")
        if not isinstance(arguments, str) or not arguments.strip():
            return False
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return False
        if not isinstance(parsed_arguments, dict):
            return False
    return saw_tool_call


def _object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    object_dict: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            return None
        object_dict[key] = item
    return object_dict
