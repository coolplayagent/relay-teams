# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
from collections.abc import Awaitable

import pytest

from relay_teams.interfaces.server.async_call import call_maybe_async


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
