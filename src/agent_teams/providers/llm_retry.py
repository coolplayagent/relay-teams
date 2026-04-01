# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError

from agent_teams.providers.model_config import LlmRetryConfig

try:
    from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
except ImportError:  # pragma: no cover
    APIConnectionError = None
    APIError = None
    APIStatusError = None
    APITimeoutError = None

T = TypeVar("T")


class LlmRetryErrorInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str
    status_code: int | None = None
    error_code: str | None = None
    error_type: str | None = None
    retry_after_ms: int | None = Field(default=None, ge=0)
    retryable: bool = False
    transport_error: bool = False
    timeout_error: bool = False


class LlmRetrySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retry_number: int = Field(ge=1)
    next_attempt_number: int = Field(ge=2)
    total_attempts: int = Field(ge=1)
    delay_ms: int = Field(ge=0)
    error: LlmRetryErrorInfo


class LlmRetryExhaustedError(Exception):
    def __init__(self, *, error: LlmRetryErrorInfo, retries_used: int) -> None:
        self.error = error
        self.retries_used = retries_used
        super().__init__(error.message)


async def run_with_llm_retry(
    *,
    operation: Callable[[], Awaitable[T]],
    config: LlmRetryConfig,
    is_retry_allowed: Callable[[], bool],
    on_retry_scheduled: Callable[[LlmRetrySchedule], Awaitable[None] | None],
    on_retry_exhausted: Callable[[LlmRetryExhaustedError], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    attempt_number = 1
    retries_used = 0
    total_attempts = config.max_retries + 1

    while True:
        try:
            return await operation()
        except Exception as exc:
            error = extract_retry_error_info(exc)
            if (
                not config.enabled
                or error is None
                or not error.retryable
                or retries_used >= config.max_retries
                or not is_retry_allowed()
            ):
                if error is not None and retries_used >= config.max_retries:
                    exhausted = LlmRetryExhaustedError(
                        error=error,
                        retries_used=retries_used,
                    )
                    if on_retry_exhausted is not None:
                        on_retry_exhausted(exhausted)
                raise

            retries_used += 1
            attempt_number += 1
            delay_ms = compute_retry_delay_ms(
                config=config,
                retry_number=retries_used,
            )
            schedule = LlmRetrySchedule(
                retry_number=retries_used,
                next_attempt_number=attempt_number,
                total_attempts=total_attempts,
                delay_ms=delay_ms,
                error=error,
            )
            maybe_awaitable = on_retry_scheduled(schedule)
            if maybe_awaitable is not None:
                await maybe_awaitable
            await sleep(delay_ms / 1000)


def compute_retry_delay_ms(
    *,
    config: LlmRetryConfig,
    retry_number: int,
) -> int:
    base_delay_ms = int(
        config.initial_delay_ms * (config.backoff_multiplier ** (retry_number - 1))
    )
    resolved_delay_ms = max(0, base_delay_ms)
    if not config.jitter or resolved_delay_ms == 0:
        return resolved_delay_ms
    jitter_ratio = 0.2
    lower_bound = max(0, int(resolved_delay_ms * (1 - jitter_ratio)))
    upper_bound = max(lower_bound, int(resolved_delay_ms * (1 + jitter_ratio)))
    return random.randint(lower_bound, upper_bound)


def extract_retry_error_info(exc: BaseException) -> LlmRetryErrorInfo | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        resolved = _extract_single_error_info(current)
        if resolved is not None:
            return resolved
        current = current.__cause__ or current.__context__
    return None


def _extract_single_error_info(exc: BaseException) -> LlmRetryErrorInfo | None:
    invalid_json_error = _extract_invalid_json_error_info(exc)
    if invalid_json_error is not None:
        return invalid_json_error
    if isinstance(exc, ModelHTTPError):
        retryable = _is_retryable_status_code(exc.status_code)
        return LlmRetryErrorInfo(
            message=str(exc),
            status_code=exc.status_code,
            error_code=_status_code_error_code(exc.status_code),
            retryable=retryable,
        )
    if isinstance(exc, ModelAPIError):
        parsed = _parse_message_metadata(str(exc))
        if parsed is None:
            return None
        return LlmRetryErrorInfo(
            message=str(exc),
            status_code=parsed.status_code,
            error_code=parsed.error_code,
            retryable=_is_retryable_status_code(parsed.status_code),
        )
    if isinstance(exc, httpx.TimeoutException):
        return LlmRetryErrorInfo(
            message=str(exc) or "Connection timed out.",
            error_code="network_timeout",
            retryable=True,
            transport_error=True,
            timeout_error=True,
        )
    if isinstance(exc, httpx.RemoteProtocolError):
        return LlmRetryErrorInfo(
            message=str(exc) or "Stream transport was interrupted.",
            error_code="network_stream_interrupted",
            retryable=True,
            transport_error=True,
        )
    if isinstance(exc, httpx.RequestError):
        return LlmRetryErrorInfo(
            message=str(exc) or "Network request failed.",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        )

    if APITimeoutError is not None and isinstance(exc, APITimeoutError):
        return LlmRetryErrorInfo(
            message=str(exc) or "Connection timed out.",
            error_code="network_timeout",
            retryable=True,
            transport_error=True,
            timeout_error=True,
        )
    if APIConnectionError is not None and isinstance(exc, APIConnectionError):
        return LlmRetryErrorInfo(
            message=str(exc) or "Network request failed.",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        )
    if APIStatusError is not None and isinstance(exc, APIStatusError):
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        body = getattr(exc, "body", None)
        error_payload = _extract_error_payload(body)
        status_code = getattr(exc, "status_code", None)
        retry_override = _explicit_retry_override(headers)
        return LlmRetryErrorInfo(
            message=error_payload.message or str(exc),
            status_code=status_code,
            error_code=error_payload.error_code or _status_code_error_code(status_code),
            error_type=error_payload.error_type,
            retry_after_ms=_parse_retry_after_ms(headers.get("retry-after"))
            if headers is not None
            else None,
            retryable=(
                retry_override
                if retry_override is not None
                else _is_retryable_status_code(status_code)
            ),
        )
    if APIError is not None and isinstance(exc, APIError):
        body = getattr(exc, "body", None)
        error_payload = _extract_error_payload(body)
        fallback = _parse_message_metadata(str(exc))
        status_code = fallback.status_code if fallback is not None else None
        retry_override = _explicit_retry_override(getattr(exc, "headers", None))
        return LlmRetryErrorInfo(
            message=error_payload.message or str(exc),
            status_code=status_code,
            error_code=error_payload.error_code
            or _optional_str(getattr(exc, "code", None))
            or (fallback.error_code if fallback is not None else None),
            error_type=error_payload.error_type,
            retryable=(
                retry_override
                if retry_override is not None
                else _is_retryable_status_code(status_code)
            ),
        )

    return None


def _extract_invalid_json_error_info(
    exc: BaseException,
) -> LlmRetryErrorInfo | None:
    if not isinstance(exc, json.JSONDecodeError):
        return None
    return LlmRetryErrorInfo(
        message=str(exc),
        error_code="model_tool_args_invalid_json",
        retryable=False,
        transport_error=False,
        timeout_error=False,
    )


class _ParsedErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str | None = None
    error_code: str | None = None
    error_type: str | None = None


class _ParsedMessageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status_code: int | None = None
    error_code: str | None = None


def _extract_error_payload(body: object) -> _ParsedErrorPayload:
    if not isinstance(body, dict):
        return _ParsedErrorPayload()
    error_payload = body.get("error")
    if isinstance(error_payload, dict):
        return _ParsedErrorPayload(
            message=_optional_str(error_payload.get("message")),
            error_code=_optional_str(error_payload.get("code")),
            error_type=_optional_str(error_payload.get("type")),
        )
    return _ParsedErrorPayload(
        message=_optional_str(body.get("message")) or _optional_str(body.get("detail")),
        error_code=_optional_str(body.get("code")),
        error_type=_optional_str(body.get("type")),
    )


def _parse_message_metadata(message: str) -> _ParsedMessageMetadata | None:
    matched_status = re.search(r"status_code:\s*(\d{3})", message)
    if matched_status is not None:
        status_code = int(matched_status.group(1))
        return _ParsedMessageMetadata(
            status_code=status_code,
            error_code=_status_code_error_code(status_code),
        )

    matched_code = re.search(r"\bcode\b\s*[:=]\s*([A-Za-z0-9_.-]+)", message)
    if matched_code is not None:
        return _ParsedMessageMetadata(error_code=matched_code.group(1))

    trailing_code = re.search(r"\(([A-Za-z0-9_.-]{2,64})\)\s*$", message.strip())
    if trailing_code is not None:
        return _ParsedMessageMetadata(error_code=trailing_code.group(1))
    return None


def _optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _status_code_error_code(status_code: int | None) -> str | None:
    if status_code is None:
        return None
    if status_code in {401, 403}:
        return "auth_invalid"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "provider_error"
    return "request_invalid"


def _is_retryable_status_code(status_code: int | None) -> bool:
    if status_code is None:
        return False
    return status_code in {408, 409, 429} or status_code >= 500


def _explicit_retry_override(headers: object) -> bool | None:
    if headers is None:
        return None
    raw_value: object
    if isinstance(headers, dict):
        raw_value = headers.get("x-should-retry")
        if raw_value is None:
            raw_value = headers.get("X-Should-Retry")
    else:
        getter = getattr(headers, "get", None)
        if getter is None:
            return None
        raw_value = getter("x-should-retry")
        if raw_value is None:
            raw_value = getter("X-Should-Retry")
    normalized = _optional_str(raw_value)
    if normalized is None:
        return None
    lowered = normalized.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return None


def _parse_retry_after_ms(raw_value: str | None) -> int | None:
    normalized = _optional_str(raw_value)
    if normalized is None:
        return None
    if normalized.isdigit():
        return max(0, int(normalized) * 1000)
    try:
        parsed_at = parsedate_to_datetime(normalized)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed_at.tzinfo is None:
        return None
    now = datetime.now(timezone.utc)
    delay_seconds = int((parsed_at - now).total_seconds())
    return max(0, delay_seconds * 1000)
