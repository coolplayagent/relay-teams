from __future__ import annotations

import asyncio
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
