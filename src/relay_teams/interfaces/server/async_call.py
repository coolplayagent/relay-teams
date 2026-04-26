from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import inspect
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")
ISOLATED_THREAD_WORKER_COUNT = 8
_ISOLATED_THREAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=ISOLATED_THREAD_WORKER_COUNT,
    thread_name_prefix="server-isolated-route",
)


async def call_maybe_async(
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    if inspect.iscoroutinefunction(function):
        result = function(*args, **kwargs)
    else:
        result = await asyncio.to_thread(function, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


async def call_maybe_async_in_isolated_thread(
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    if inspect.iscoroutinefunction(function):
        result = function(*args, **kwargs)
    else:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _ISOLATED_THREAD_EXECUTOR,
            partial(function, *args, **kwargs),
        )
    if inspect.isawaitable(result):
        return await result
    return result
