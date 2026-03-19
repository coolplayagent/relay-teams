# -*- coding: utf-8 -*-
from __future__ import annotations

import httpx
import pytest
from openai import APIError, APIStatusError

from agent_teams.providers.llm_retry import (
    compute_retry_delay_ms,
    extract_retry_error_info,
    run_with_llm_retry,
)
from agent_teams.providers.model_config import LlmRetryConfig


def test_extract_retry_error_info_reads_status_code_and_retry_after() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(
        429,
        headers={"Retry-After": "7"},
        request=request,
    )
    exc = APIStatusError(
        "rate limited",
        response=response,
        body={"error": {"code": "rate_limited", "message": "slow down"}},
    )

    info = extract_retry_error_info(exc)

    assert info is not None
    assert info.status_code == 429
    assert info.error_code == "rate_limited"
    assert info.retry_after_ms == 7000


def test_extract_retry_error_info_reads_provider_code_without_status() -> None:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    exc = APIError(
        "provider error",
        request=request,
        body={"error": {"code": "2062", "message": "busy"}},
    )

    info = extract_retry_error_info(exc)

    assert info is not None
    assert info.status_code is None
    assert info.error_code == "2062"
    assert info.message == "busy"


def test_compute_retry_delay_ms_uses_retry_after_without_jitter() -> None:
    config = LlmRetryConfig(
        jitter=False,
        respect_retry_after=True,
        initial_delay_ms=1000,
        max_delay_ms=30000,
    )

    delay_ms = compute_retry_delay_ms(
        config=config,
        retry_number=2,
        retry_after_ms=7000,
    )

    assert delay_ms == 7000


@pytest.mark.asyncio
async def test_run_with_llm_retry_retries_until_success() -> None:
    attempts = {"count": 0}
    recorded_delays: list[int] = []

    async def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            raise APIError(
                "provider error",
                request=request,
                body={"error": {"code": "2062", "message": "busy"}},
            )
        return "ok"

    result = await run_with_llm_retry(
        operation=operation,
        config=LlmRetryConfig(jitter=False, max_retries=5, initial_delay_ms=1000),
        is_retry_allowed=lambda: True,
        on_retry_scheduled=lambda schedule: recorded_delays.append(schedule.delay_ms),
        sleep=lambda _seconds: _async_noop(),
    )

    assert result == "ok"
    assert attempts["count"] == 3
    assert recorded_delays == [1000, 2000]


async def _async_noop() -> None:
    return None
