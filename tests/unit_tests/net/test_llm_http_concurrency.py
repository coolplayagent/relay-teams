# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from relay_teams.net.llm_http_concurrency import (
    DEFAULT_LLM_HTTP_MAX_CONCURRENCY,
    LLM_HTTP_MAX_CONCURRENCY_ENV,
    LlmHttpConcurrencyLimiter,
    resolve_llm_http_max_concurrency,
)


@pytest.mark.asyncio
async def test_limiter_blocks_same_origin_until_lease_released() -> None:
    limiter = LlmHttpConcurrencyLimiter(max_concurrency=1)
    first_lease = await limiter.acquire("https://provider.example/v1/chat/completions")
    waiter = asyncio.create_task(
        limiter.acquire("https://provider.example/v1/models"),
    )

    await asyncio.sleep(0)

    assert waiter.done() is False

    first_lease.release()
    second_lease = await asyncio.wait_for(waiter, timeout=1.0)

    second_lease.release()


@pytest.mark.asyncio
async def test_limiter_allows_different_origins_independently() -> None:
    limiter = LlmHttpConcurrencyLimiter(max_concurrency=1)
    first_lease = await limiter.acquire(
        "https://provider-a.example/v1/chat/completions"
    )

    second_lease = await asyncio.wait_for(
        limiter.acquire("https://provider-b.example/v1/chat/completions"),
        timeout=1.0,
    )

    second_lease.release()
    first_lease.release()


def test_resolve_llm_http_max_concurrency_defaults_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LLM_HTTP_MAX_CONCURRENCY_ENV, raising=False)

    assert resolve_llm_http_max_concurrency() == DEFAULT_LLM_HTTP_MAX_CONCURRENCY


def test_resolve_llm_http_max_concurrency_allows_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LLM_HTTP_MAX_CONCURRENCY_ENV, "0")

    assert resolve_llm_http_max_concurrency() is None
