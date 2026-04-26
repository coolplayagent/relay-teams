from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import inspect
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")


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
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="server-isolated-route",
        )
        try:
            result = await loop.run_in_executor(
                executor,
                partial(function, *args, **kwargs),
            )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    if inspect.isawaitable(result):
        return await result
    return result
