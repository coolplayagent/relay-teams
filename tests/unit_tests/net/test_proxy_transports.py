# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
import json

import httpx
import pytest

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.net.proxy_transports import (
    AsyncProxyRoutingTransport,
    _chunk_has_complete_tool_call_delta,
    _chunk_has_terminal_sse_marker,
    _payload_has_complete_tool_call_delta,
    _tool_call_deltas_have_complete_arguments,
)


class _FakeLease:
    def __init__(self) -> None:
        self.release_count = 0

    def release(self) -> None:
        self.release_count += 1


class _FakeLimiter:
    def __init__(self) -> None:
        self.lease = _FakeLease()
        self.acquired_urls: list[str] = []

    async def acquire(self, url: str) -> _FakeLease:
        self.acquired_urls.append(url)
        return self.lease


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: tuple[bytes, ...] = (b"ok",),
        *,
        fail_after_first_chunk: bool = False,
    ) -> None:
        self._chunks = chunks
        self._fail_after_first_chunk = fail_after_first_chunk
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self._chunks):
            yield chunk
            if self._fail_after_first_chunk and index == 0:
                raise RuntimeError("stream failed")

    async def aclose(self) -> None:
        self.closed = True


class _SyncStream(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"ok"


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        stream: httpx.AsyncByteStream | httpx.SyncByteStream | None = None,
        raise_on_request: bool = False,
    ) -> None:
        self.stream = stream
        self.raise_on_request = raise_on_request
        self.closed = False
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_on_request:
            raise RuntimeError("request failed")
        return httpx.Response(
            200,
            stream=self.stream or _ChunkStream(),
            request=request,
        )

    async def aclose(self) -> None:
        self.closed = True


def _build_transport(
    *,
    limiter: _FakeLimiter | None = None,
    direct_transport: _FakeTransport | None = None,
) -> tuple[AsyncProxyRoutingTransport, _FakeTransport]:
    transport = AsyncProxyRoutingTransport(
        ProxyEnvConfig(),
        ssl_verify=False,
        request_limiter=limiter,
    )
    fake_transport = direct_transport or _FakeTransport()
    setattr(transport, "_direct_transport", fake_transport)
    return transport, fake_transport


def test_terminal_sse_marker_helpers_cover_non_terminal_payloads() -> None:
    assert _chunk_has_terminal_sse_marker(b"data: [DONE]\n\n") is True
    assert (
        _chunk_has_terminal_sse_marker(
            b'data: {"choices":[{"finish_reason":null}]}\n\n'
        )
        is False
    )
    assert (
        _chunk_has_terminal_sse_marker(
            b'data: {"choices":[{"delta":{"content":{"finish_reason":"stop"}},'
            b'"finish_reason":null}]}\n\n'
        )
        is False
    )
    assert _chunk_has_complete_tool_call_delta(b"event: ping\n\n") is False
    assert _payload_has_complete_tool_call_delta([]) is False
    assert _payload_has_complete_tool_call_delta({"choices": "bad"}) is False
    assert _payload_has_complete_tool_call_delta({"choices": [None]}) is False
    assert (
        _payload_has_complete_tool_call_delta({"choices": [{"delta": None}]}) is False
    )
    assert (
        _payload_has_complete_tool_call_delta(
            {"choices": [{"delta": {"tool_calls": []}}]}
        )
        is False
    )


def test_tool_call_delta_argument_validation_rejects_incomplete_payloads() -> None:
    assert _tool_call_deltas_have_complete_arguments([None]) is False
    assert _tool_call_deltas_have_complete_arguments([{}]) is False
    assert _tool_call_deltas_have_complete_arguments([{"function": {}}]) is False
    assert (
        _tool_call_deltas_have_complete_arguments(
            [{"function": {"arguments": "{not-json"}}]
        )
        is False
    )
    assert (
        _tool_call_deltas_have_complete_arguments(
            [{"function": {"arguments": "[1, 2]"}}]
        )
        is False
    )


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_after_stream_consumed() -> None:
    limiter = _FakeLimiter()
    stream = _ChunkStream(chunks=(b"o", b"k"))
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )

    assert limiter.acquired_urls == ["https://provider.example/v1/chat/completions"]
    assert limiter.lease.release_count == 0
    assert await response.aread() == b"ok"
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_on_sse_finish_reason() -> None:
    limiter = _FakeLimiter()
    stream = _ChunkStream(
        chunks=(
            b'data: {"choices":[{"finish_reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        )
    )
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    iterator = response.aiter_bytes().__aiter__()

    assert await anext(iterator) == b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'
    assert limiter.lease.release_count == 1

    await response.aclose()
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_on_split_sse_finish_reason() -> (
    None
):
    limiter = _FakeLimiter()
    stream = _ChunkStream(
        chunks=(
            b'data: {"choices":[{"finish_',
            b'reason":"stop"}]}\n\n',
            b"data: [DONE]\n\n",
        )
    )
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    iterator = response.aiter_bytes().__aiter__()

    assert await anext(iterator) == b'data: {"choices":[{"finish_'
    assert limiter.lease.release_count == 0
    assert await anext(iterator) == b'reason":"stop"}]}\n\n'
    assert limiter.lease.release_count == 1

    await response.aclose()
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_on_complete_tool_call_delta() -> (
    None
):
    limiter = _FakeLimiter()
    payload = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "orch_dispatch_task",
                                "arguments": json.dumps({"task_id": "task-1"}),
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    first_chunk = f"data: {json.dumps(payload)}\n\n".encode()
    stream = _ChunkStream(
        chunks=(
            first_chunk,
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
            b"data: [DONE]\n\n",
        )
    )
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    iterator = response.aiter_bytes().__aiter__()

    assert await anext(iterator) == first_chunk
    assert limiter.lease.release_count == 1

    await response.aclose()
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_on_split_complete_tool_call_delta() -> (
    None
):
    limiter = _FakeLimiter()
    payload = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "orch_dispatch_task",
                                "arguments": json.dumps({"payload": "x" * 800}),
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    full_chunk = f"data: {json.dumps(payload)}\n\n".encode()
    first_chunk = full_chunk[:320]
    second_chunk = full_chunk[320:]
    stream = _ChunkStream(chunks=(first_chunk, second_chunk))
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    iterator = response.aiter_bytes().__aiter__()

    assert await anext(iterator) == first_chunk
    assert limiter.lease.release_count == 0
    assert await anext(iterator) == second_chunk
    assert limiter.lease.release_count == 1

    await response.aclose()
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_keeps_limiter_for_partial_tool_call_delta() -> (
    None
):
    limiter = _FakeLimiter()
    payload = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "orch_dispatch_task",
                                "arguments": '{"task_id":',
                            },
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    first_chunk = f"data: {json.dumps(payload)}\n\n".encode()
    final_chunk = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
    stream = _ChunkStream(chunks=(first_chunk, final_chunk))
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    iterator = response.aiter_bytes().__aiter__()

    assert await anext(iterator) == first_chunk
    assert limiter.lease.release_count == 0
    assert await anext(iterator) == final_chunk
    assert limiter.lease.release_count == 1

    await response.aclose()
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_when_response_closed() -> None:
    limiter = _FakeLimiter()
    stream = _ChunkStream()
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )
    await response.aclose()
    await response.aclose()

    assert stream.closed is True
    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_when_delegate_raises() -> None:
    limiter = _FakeLimiter()
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(raise_on_request=True),
    )

    with pytest.raises(RuntimeError, match="request failed"):
        await transport.handle_async_request(
            httpx.Request("GET", "https://provider.example/v1/chat/completions")
        )

    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_for_invalid_stream_type() -> None:
    limiter = _FakeLimiter()
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=_SyncStream()),
    )

    with pytest.raises(TypeError, match="non-async response stream"):
        await transport.handle_async_request(
            httpx.Request("GET", "https://provider.example/v1/chat/completions")
        )

    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_releases_limiter_when_stream_iteration_fails() -> (
    None
):
    limiter = _FakeLimiter()
    stream = _ChunkStream(fail_after_first_chunk=True)
    transport, _ = _build_transport(
        limiter=limiter,
        direct_transport=_FakeTransport(stream=stream),
    )

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )

    with pytest.raises(RuntimeError, match="stream failed"):
        await response.aread()

    assert limiter.lease.release_count == 1


@pytest.mark.asyncio
async def test_async_proxy_transport_without_limiter_delegates_directly() -> None:
    transport, fake_transport = _build_transport()

    response = await transport.handle_async_request(
        httpx.Request("GET", "https://provider.example/v1/chat/completions")
    )

    assert await response.aread() == b"ok"
    assert len(fake_transport.requests) == 1
