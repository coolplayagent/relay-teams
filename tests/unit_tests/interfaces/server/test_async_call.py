# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import gc
import threading
from collections.abc import Awaitable
from concurrent.futures import ThreadPoolExecutor

import pytest

from relay_teams.interfaces.server import async_call
from relay_teams.interfaces.server.async_call import (
    call_maybe_async,
    call_maybe_async_in_isolated_thread,
    call_maybe_async_in_session_read_thread,
    call_route_work,
    RouteWorkClass,
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


def test_route_work_class_for_http_method_keeps_reads_off_control_lane() -> None:
    assert async_call.route_work_class_for_http_method("GET") is RouteWorkClass.UI_READ
    assert async_call.route_work_class_for_http_method("head") is RouteWorkClass.UI_READ
    assert (
        async_call.route_work_class_for_http_method("POST")
        is RouteWorkClass.CRITICAL_CONTROL
    )
    assert (
        async_call.route_work_class_for_http_method("PATCH")
        is RouteWorkClass.CRITICAL_CONTROL
    )


@pytest.mark.asyncio
async def test_call_maybe_async_uses_bound_route_work_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        async_call._ROUTE_SEMAPHORES,
        RouteWorkClass.UI_READ,
        asyncio.Semaphore(0),
    )
    monkeypatch.setattr(async_call, "DEFAULT_ROUTE_WORK_QUEUE_TIMEOUT_SECONDS", 0.01)
    token = async_call.set_default_route_work_class(RouteWorkClass.CRITICAL_CONTROL)

    try:
        assert await call_maybe_async(lambda: "ok") == "ok"
    finally:
        async_call.reset_default_route_work_class(token)


@pytest.mark.asyncio
async def test_call_maybe_async_is_not_blocked_by_default_executor() -> None:
    loop = asyncio.get_running_loop()
    release_default_worker = threading.Event()
    default_worker_started = threading.Event()

    def block_default_worker() -> None:
        default_worker_started.set()
        _ = release_default_worker.wait(timeout=5.0)

    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    blocking_task = asyncio.create_task(asyncio.to_thread(block_default_worker))

    try:
        for _ in range(50):
            if default_worker_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert default_worker_started.is_set() is True

        def load_value() -> str:
            return "ok"

        assert await asyncio.wait_for(call_maybe_async(load_value), timeout=1.0) == "ok"
    finally:
        release_default_worker.set()
        completed = await blocking_task
        assert completed is None


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


@pytest.mark.asyncio
async def test_session_read_thread_is_not_blocked_by_default_executor() -> None:
    loop = asyncio.get_running_loop()
    release_default_worker = threading.Event()
    default_worker_started = threading.Event()

    def block_default_worker() -> None:
        default_worker_started.set()
        _ = release_default_worker.wait(timeout=5.0)

    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    blocking_task = asyncio.create_task(asyncio.to_thread(block_default_worker))

    try:
        for _ in range(50):
            if default_worker_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert default_worker_started.is_set() is True

        def load_value() -> tuple[str, bool]:
            return "ok", threading.get_ident() != threading.main_thread().ident

        result = await asyncio.wait_for(
            call_maybe_async_in_session_read_thread(
                "test.session_read",
                load_value,
            ),
            timeout=1.0,
        )

        assert result == ("ok", True)
    finally:
        release_default_worker.set()
        completed = await blocking_task
        assert completed is None


@pytest.mark.asyncio
async def test_call_route_work_keeps_queue_slot_until_canceled_thread_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = asyncio.Semaphore(1)
    worker_started = threading.Event()
    release_worker = threading.Event()

    monkeypatch.setitem(
        async_call._ROUTE_EXECUTORS,
        RouteWorkClass.UI_READ,
        executor,
    )
    monkeypatch.setitem(
        async_call._ROUTE_SEMAPHORES,
        RouteWorkClass.UI_READ,
        semaphore,
    )

    def block_worker() -> str:
        worker_started.set()
        if not release_worker.wait(timeout=5.0):
            return "timed out"
        return "done"

    slow_task = asyncio.create_task(
        call_route_work(RouteWorkClass.UI_READ, "test.blocking", block_worker)
    )
    try:
        started = await asyncio.to_thread(worker_started.wait, 1.0)
        assert started is True

        slow_task.cancel()
        slow_result = await asyncio.gather(slow_task, return_exceptions=True)
        assert isinstance(slow_result[0], asyncio.CancelledError)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                call_route_work(RouteWorkClass.UI_READ, "test.fast", lambda: "fast"),
                timeout=0.05,
            )

        release_worker.set()
        assert (
            await asyncio.wait_for(
                call_route_work(RouteWorkClass.UI_READ, "test.fast", lambda: "fast"),
                timeout=1.0,
            )
            == "fast"
        )
    finally:
        release_worker.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_call_route_work_drains_canceled_thread_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = asyncio.Semaphore(1)
    worker_started = threading.Event()
    release_worker = threading.Event()
    loop = asyncio.get_running_loop()
    captured_contexts: list[dict[str, object]] = []
    original_exception_handler = loop.get_exception_handler()

    monkeypatch.setitem(
        async_call._ROUTE_EXECUTORS,
        RouteWorkClass.UI_READ,
        executor,
    )
    monkeypatch.setitem(
        async_call._ROUTE_SEMAPHORES,
        RouteWorkClass.UI_READ,
        semaphore,
    )

    def capture_loop_exception(
        event_loop: asyncio.AbstractEventLoop,
        context: dict[str, object],
    ) -> None:
        _ = event_loop
        captured_contexts.append(context)

    def fail_worker() -> str:
        worker_started.set()
        if not release_worker.wait(timeout=5.0):
            return "timed out"
        raise RuntimeError("background failure")

    loop.set_exception_handler(capture_loop_exception)
    slow_task = asyncio.create_task(
        call_route_work(RouteWorkClass.UI_READ, "test.failing", fail_worker)
    )
    try:
        started = await asyncio.to_thread(worker_started.wait, 1.0)
        assert started is True

        slow_task.cancel()
        slow_result = await asyncio.gather(slow_task, return_exceptions=True)
        assert isinstance(slow_result[0], asyncio.CancelledError)

        release_worker.set()
        assert (
            await asyncio.wait_for(
                call_route_work(RouteWorkClass.UI_READ, "test.fast", lambda: "fast"),
                timeout=1.0,
            )
            == "fast"
        )
        gc.collect()
        await asyncio.sleep(0)
        assert captured_contexts == []
    finally:
        loop.set_exception_handler(original_exception_handler)
        release_worker.set()
        executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_call_route_work_rejects_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    semaphore = asyncio.Semaphore(0)
    monkeypatch.setitem(
        async_call._ROUTE_SEMAPHORES,
        RouteWorkClass.UI_READ,
        semaphore,
    )
    monkeypatch.setattr(async_call, "DEFAULT_ROUTE_WORK_QUEUE_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(async_call.RouteWorkRejectedError):
        await call_route_work(RouteWorkClass.UI_READ, "test.rejected", lambda: "fast")


@pytest.mark.asyncio
async def test_call_route_work_supports_async_functions_and_slow_call_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged_events: list[str] = []

    async def load_value() -> str:
        await asyncio.sleep(0)
        return "ok"

    def capture_log_event(*args: object, **kwargs: object) -> None:
        _ = args
        event = kwargs.get("event")
        if isinstance(event, str):
            logged_events.append(event)

    monkeypatch.setattr(async_call, "SLOW_SERVER_CALL_SECONDS", 0)
    monkeypatch.setattr(async_call, "log_event", capture_log_event)

    result = await call_route_work(RouteWorkClass.UI_READ, "test.async", load_value)

    assert result == "ok"
    assert "server.route_work.slow_call" in logged_events
