# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable

import pytest

from relay_teams.interfaces.server import async_call
from relay_teams.interfaces.server.async_call import (
    call_maybe_async,
    call_maybe_async_in_isolated_thread,
)


@pytest.mark.asyncio
async def test_call_maybe_async_awaits_coroutine_function() -> None:
    async def load_value(value: str) -> str:
        return f"async:{value}"

    assert await call_maybe_async(load_value, "ok") == "async:ok"


@pytest.mark.asyncio
async def test_call_maybe_async_runs_sync_function_off_loop_thread() -> None:
    loop_thread_id = threading.get_ident()

    def load_value(value: str) -> tuple[str, bool]:
        return value, threading.get_ident() != loop_thread_id

    assert await call_maybe_async(load_value, "ok") == ("ok", True)


@pytest.mark.asyncio
async def test_call_maybe_async_awaits_sync_function_returned_awaitable() -> None:
    async def load_value() -> str:
        return "ok"

    def make_awaitable() -> Awaitable[str]:
        return load_value()

    assert await call_maybe_async(make_awaitable) == "ok"


@pytest.mark.asyncio
async def test_call_maybe_async_in_isolated_thread_uses_bounded_pool() -> None:
    loop_thread_id = threading.get_ident()
    worker_thread_ids: set[int] = set()
    lock = threading.Lock()
    workers_started = threading.Event()
    release_workers = threading.Event()

    def load_value(index: int) -> tuple[int, bool]:
        with lock:
            worker_thread_ids.add(threading.get_ident())
            if len(worker_thread_ids) >= async_call.ISOLATED_THREAD_WORKER_COUNT:
                workers_started.set()
        _ = release_workers.wait(timeout=5.0)
        return index, threading.get_ident() != loop_thread_id

    tasks = [
        asyncio.create_task(call_maybe_async_in_isolated_thread(load_value, index))
        for index in range(async_call.ISOLATED_THREAD_WORKER_COUNT + 4)
    ]

    try:
        for _ in range(50):
            if workers_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert workers_started.is_set() is True
    finally:
        release_workers.set()

    results = await asyncio.gather(*tasks)

    assert all(is_worker_thread for _, is_worker_thread in results)
    assert len(worker_thread_ids) <= async_call.ISOLATED_THREAD_WORKER_COUNT
