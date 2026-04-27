from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, Token
from enum import Enum
from functools import partial
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar, cast

from relay_teams.logger import get_logger, log_event

ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")
ISOLATED_THREAD_WORKER_COUNT = 8
SESSION_READ_THREAD_WORKER_COUNT = 8
UI_READ_THREAD_WORKER_COUNT = 8
SETTINGS_ADMIN_THREAD_WORKER_COUNT = 4
LOGS_INGEST_THREAD_WORKER_COUNT = 2
FILE_MEDIA_THREAD_WORKER_COUNT = 4
RUNTIME_BACKGROUND_THREAD_WORKER_COUNT = 4
SLOW_SERVER_CALL_SECONDS = 1.0
DEFAULT_ROUTE_WORK_QUEUE_TIMEOUT_SECONDS = 4.0
LOGGER = get_logger(__name__)


class RouteWorkClass(str, Enum):
    CRITICAL_CONTROL = "critical_control"
    SESSION_READ = "session_read"
    UI_READ = "ui_read"
    SETTINGS_ADMIN = "settings_admin"
    LOGS_INGEST = "logs_ingest"
    FILE_MEDIA = "file_media"
    RUNTIME_BACKGROUND = "runtime_background"


class RouteWorkRejectedError(RuntimeError):
    pass


_ROUTE_WORKER_COUNTS = {
    RouteWorkClass.CRITICAL_CONTROL: ISOLATED_THREAD_WORKER_COUNT,
    RouteWorkClass.SESSION_READ: SESSION_READ_THREAD_WORKER_COUNT,
    RouteWorkClass.UI_READ: UI_READ_THREAD_WORKER_COUNT,
    RouteWorkClass.SETTINGS_ADMIN: SETTINGS_ADMIN_THREAD_WORKER_COUNT,
    RouteWorkClass.LOGS_INGEST: LOGS_INGEST_THREAD_WORKER_COUNT,
    RouteWorkClass.FILE_MEDIA: FILE_MEDIA_THREAD_WORKER_COUNT,
    RouteWorkClass.RUNTIME_BACKGROUND: RUNTIME_BACKGROUND_THREAD_WORKER_COUNT,
}
_ROUTE_QUEUE_LIMITS = {
    RouteWorkClass.CRITICAL_CONTROL: ISOLATED_THREAD_WORKER_COUNT * 2,
    RouteWorkClass.SESSION_READ: SESSION_READ_THREAD_WORKER_COUNT * 10,
    RouteWorkClass.UI_READ: UI_READ_THREAD_WORKER_COUNT * 8,
    RouteWorkClass.SETTINGS_ADMIN: SETTINGS_ADMIN_THREAD_WORKER_COUNT * 2,
    RouteWorkClass.LOGS_INGEST: LOGS_INGEST_THREAD_WORKER_COUNT * 2,
    RouteWorkClass.FILE_MEDIA: FILE_MEDIA_THREAD_WORKER_COUNT * 2,
    RouteWorkClass.RUNTIME_BACKGROUND: RUNTIME_BACKGROUND_THREAD_WORKER_COUNT * 2,
}
_ROUTE_EXECUTORS = {
    work_class: ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=f"server-{work_class.value.replace('_', '-')}",
    )
    for work_class, worker_count in _ROUTE_WORKER_COUNTS.items()
}
_ROUTE_SEMAPHORES = {
    work_class: asyncio.Semaphore(_ROUTE_QUEUE_LIMITS[work_class])
    for work_class in RouteWorkClass
}
_DEFAULT_ROUTE_WORK_CLASS: ContextVar[RouteWorkClass | None] = ContextVar(
    "default_route_work_class",
    default=None,
)


def route_work_class_for_http_method(method: str) -> RouteWorkClass:
    safe_method = method.upper()
    if safe_method in {"GET", "HEAD", "OPTIONS"}:
        return RouteWorkClass.UI_READ
    return RouteWorkClass.CRITICAL_CONTROL


def set_default_route_work_class(
    work_class: RouteWorkClass,
) -> Token[RouteWorkClass | None]:
    return _DEFAULT_ROUTE_WORK_CLASS.set(work_class)


def reset_default_route_work_class(token: Token[RouteWorkClass | None]) -> None:
    _DEFAULT_ROUTE_WORK_CLASS.reset(token)


def get_default_route_work_class() -> RouteWorkClass:
    return _DEFAULT_ROUTE_WORK_CLASS.get() or RouteWorkClass.UI_READ


async def call_maybe_async(
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    return await call_route_work(
        get_default_route_work_class(),
        getattr(function, "__name__", "route.call"),
        function,
        *args,
        **kwargs,
    )


async def call_route_work(
    work_class: RouteWorkClass,
    operation: str,
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    semaphore = _ROUTE_SEMAPHORES[work_class]
    try:
        await asyncio.wait_for(
            semaphore.acquire(),
            timeout=DEFAULT_ROUTE_WORK_QUEUE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="server.route_work.rejected",
            message="Server route work queue was full",
            payload={
                "work_class": work_class.value,
                "operation": operation,
                "timeout_seconds": DEFAULT_ROUTE_WORK_QUEUE_TIMEOUT_SECONDS,
            },
        )
        raise RouteWorkRejectedError(
            f"Server is busy handling {work_class.value} work"
        ) from exc

    started = time.perf_counter()
    released = False
    release_deferred = False

    def release_semaphore() -> None:
        nonlocal released
        if released:
            return
        released = True
        semaphore.release()

    def release_deferred_thread_future(
        future: asyncio.Future[ResultT | Awaitable[ResultT]],
    ) -> None:
        try:
            _ = future.exception()
        except asyncio.CancelledError:
            LOGGER.debug("Deferred route work future was canceled")
        finally:
            release_semaphore()

    try:
        if inspect.iscoroutinefunction(function):
            result = function(*args, **kwargs)
        else:
            loop = asyncio.get_running_loop()
            thread_future: asyncio.Future[ResultT | Awaitable[ResultT]] = (
                loop.run_in_executor(
                    _ROUTE_EXECUTORS[work_class],
                    partial(function, *args, **kwargs),
                )
            )
            try:
                result = await asyncio.shield(thread_future)
            except BaseException:
                if not thread_future.done():
                    release_deferred = True
                    thread_future.add_done_callback(release_deferred_thread_future)
                raise
        if inspect.isawaitable(result):
            resolved = await result
        else:
            resolved = result
    finally:
        if not release_deferred:
            release_semaphore()
    elapsed_seconds = time.perf_counter() - started
    if elapsed_seconds >= SLOW_SERVER_CALL_SECONDS:
        log_event(
            LOGGER,
            logging.WARNING,
            event="server.route_work.slow_call",
            message="Server route work call was slow",
            duration_ms=int(elapsed_seconds * 1000),
            payload={
                "work_class": work_class.value,
                "operation": operation or getattr(function, "__name__", "unknown"),
            },
        )
    # noinspection PyUnnecessaryCast
    return cast(ResultT, resolved)


async def call_maybe_async_in_isolated_thread(
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    return await call_route_work(
        RouteWorkClass.CRITICAL_CONTROL,
        getattr(function, "__name__", "critical.call"),
        function,
        *args,
        **kwargs,
    )


async def call_maybe_async_in_session_read_thread(
    operation: str,
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    return await call_route_work(
        RouteWorkClass.SESSION_READ,
        operation,
        function,
        *args,
        **kwargs,
    )
