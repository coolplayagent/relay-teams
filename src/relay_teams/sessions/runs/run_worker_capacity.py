# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
import logging
import os
import time
from typing import TypeVar

from relay_teams.logger import get_logger, log_event
from relay_teams.trace import bind_trace_context

logger = get_logger(__name__)

RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT_ENV = "RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT"
DEFAULT_RUN_WORKER_ACTIVE_LIMIT = 32

_T = TypeVar("_T")


def run_worker_active_limit() -> int:
    raw_value = os.getenv(RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT_ENV)
    if raw_value is None:
        return DEFAULT_RUN_WORKER_ACTIVE_LIMIT
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_RUN_WORKER_ACTIVE_LIMIT
    return max(1, parsed)


class RunWorkerCapacityLimiter:
    def __init__(self, *, limit: int | None = None) -> None:
        self._limit = limit if limit is not None else run_worker_active_limit()
        self._semaphore = asyncio.Semaphore(self._limit)

    @property
    def limit(self) -> int:
        return self._limit

    async def run(
        self,
        *,
        run_id: str,
        session_id: str,
        worker: Callable[[], Awaitable[_T]],
    ) -> _T:
        wait_started = time.perf_counter()
        async with self._semaphore:
            wait_ms = int((time.perf_counter() - wait_started) * 1000)
            if wait_ms > 0:
                with bind_trace_context(
                    trace_id=run_id,
                    run_id=run_id,
                    session_id=session_id,
                ):
                    log_event(
                        logger,
                        logging.INFO,
                        event="run.worker.capacity_acquired",
                        message="Run worker acquired startup capacity",
                        payload={
                            "wait_ms": wait_ms,
                            "active_limit": self._limit,
                        },
                    )
            return await worker()
