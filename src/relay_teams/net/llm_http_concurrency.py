# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import os
from threading import Lock

from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)
DEFAULT_LLM_HTTP_MAX_CONCURRENCY = 4
LLM_HTTP_MAX_CONCURRENCY_ENV = "RELAY_TEAMS_LLM_HTTP_MAX_CONCURRENCY"

_LIMITER_LOCK = Lock()
_LIMITERS: dict[int, LlmHttpConcurrencyLimiter] = {}


class LlmHttpConcurrencyLimiter:
    def __init__(self, *, max_concurrency: int) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be greater than zero")
        self._max_concurrency = max_concurrency
        self._semaphores: dict[tuple[int, str], asyncio.Semaphore] = {}
        self._lock = Lock()

    async def acquire(self, url: str) -> "LlmHttpConcurrencyLease":
        scope = _concurrency_scope_for_url(url)
        loop_id = id(asyncio.get_running_loop())
        semaphore_key = (loop_id, scope)
        with self._lock:
            semaphore = self._semaphores.get(semaphore_key)
            if semaphore is None:
                semaphore = asyncio.Semaphore(self._max_concurrency)
                self._semaphores[semaphore_key] = semaphore
        await semaphore.acquire()
        return LlmHttpConcurrencyLease(semaphore=semaphore)


class LlmHttpConcurrencyLease:
    def __init__(self, *, semaphore: asyncio.Semaphore) -> None:
        self._semaphore = semaphore
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._semaphore.release()


def resolve_llm_http_max_concurrency() -> int | None:
    raw_value = os.environ.get(LLM_HTTP_MAX_CONCURRENCY_ENV)
    if raw_value is None or not raw_value.strip():
        return DEFAULT_LLM_HTTP_MAX_CONCURRENCY
    try:
        resolved = int(raw_value.strip())
    except ValueError:
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.http_concurrency.invalid_env",
            message="Ignoring invalid LLM HTTP concurrency limit",
            payload={LLM_HTTP_MAX_CONCURRENCY_ENV: raw_value},
        )
        return DEFAULT_LLM_HTTP_MAX_CONCURRENCY
    if resolved == 0:
        return None
    if resolved < 0:
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.http_concurrency.invalid_env",
            message="Ignoring invalid LLM HTTP concurrency limit",
            payload={LLM_HTTP_MAX_CONCURRENCY_ENV: raw_value},
        )
        return DEFAULT_LLM_HTTP_MAX_CONCURRENCY
    return resolved


def get_llm_http_concurrency_limiter(
    max_concurrency: int | None,
) -> LlmHttpConcurrencyLimiter | None:
    if max_concurrency is None:
        return None
    with _LIMITER_LOCK:
        limiter = _LIMITERS.get(max_concurrency)
        if limiter is None:
            limiter = LlmHttpConcurrencyLimiter(max_concurrency=max_concurrency)
            _LIMITERS[max_concurrency] = limiter
        return limiter


def clear_llm_http_concurrency_limiters() -> None:
    with _LIMITER_LOCK:
        _LIMITERS.clear()


def _concurrency_scope_for_url(url: str) -> str:
    split = url.split("://", 1)
    if len(split) != 2:
        return url
    scheme, remainder = split
    authority = remainder.split("/", 1)[0]
    return f"{scheme}://{authority}".casefold()
