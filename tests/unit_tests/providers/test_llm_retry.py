# -*- coding: utf-8 -*-
from __future__ import annotations

import json

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


def test_extract_retry_error_info_marks_remote_protocol_interrupt_as_retryable() -> (
    None
):
    info = extract_retry_error_info(
        httpx.RemoteProtocolError("incomplete chunked read")
    )

    assert info is not None
    assert info.error_code == "network_stream_interrupted"
    assert info.retryable is True
    assert info.transport_error is True
    assert info.timeout_error is False


def test_extract_retry_error_info_marks_invalid_tool_args_json_as_retryable() -> None:
    info = extract_retry_error_info(
        json.JSONDecodeError(
            "Expecting property name enclosed in double quotes",
            "{invalid: true}",
            1,
        )
    )

    assert info is not None
    assert info.error_code == "model_tool_args_invalid_json"
    assert info.retryable is True
    assert info.transport_error is False
    assert info.timeout_error is False


def test_extract_retry_error_info_unwraps_invalid_tool_args_json_cause() -> None:
    try:
        raise json.JSONDecodeError(
            "Expecting property name enclosed in double quotes",
            "{invalid: true}",
            1,
        )
    except json.JSONDecodeError as inner:
        wrapped = RuntimeError("tool args parsing failed")
        wrapped.__cause__ = inner

    info = extract_retry_error_info(wrapped)

    assert info is not None
    assert info.error_code == "model_tool_args_invalid_json"
    assert info.retryable is True


def test_compute_retry_delay_ms_uses_exponential_backoff_without_jitter() -> None:
    config = LlmRetryConfig(
        jitter=False,
        initial_delay_ms=2000,
    )

    delay_ms = compute_retry_delay_ms(
        config=config,
        retry_number=2,
    )

    assert delay_ms == 4000


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
        config=LlmRetryConfig(jitter=False, max_retries=5, initial_delay_ms=2000),
        is_retry_allowed=lambda: True,
        on_retry_scheduled=lambda schedule: recorded_delays.append(schedule.delay_ms),
        sleep=lambda _seconds: _async_noop(),
    )

    assert result == "ok"
    assert attempts["count"] == 3
    assert recorded_delays == [2000, 4000]


@pytest.mark.asyncio
async def test_run_with_llm_retry_reports_exhausted_after_max_retries() -> None:
    recorded_delays: list[int] = []
    exhausted: list[tuple[str, int]] = []

    async def operation() -> str:
        raise APIError(
            "provider error",
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
            body={"error": {"code": "2062", "message": "busy"}},
        )

    with pytest.raises(APIError):
        await run_with_llm_retry(
            operation=operation,
            config=LlmRetryConfig(jitter=False, max_retries=2, initial_delay_ms=2000),
            is_retry_allowed=lambda: True,
            on_retry_scheduled=lambda schedule: recorded_delays.append(
                schedule.delay_ms
            ),
            on_retry_exhausted=lambda error: exhausted.append(
                (error.error.message, error.retries_used)
            ),
            sleep=lambda _seconds: _async_noop(),
        )

    assert recorded_delays == [2000, 4000]
    assert exhausted == [("busy", 2)]


async def _async_noop() -> None:
    return None
