# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from relay_teams.sessions.runs.run_worker_capacity import RunWorkerCapacityLimiter
from relay_teams.sessions.runs.run_worker_capacity import run_worker_active_limit


@pytest.mark.asyncio
async def test_run_worker_capacity_limiter_caps_concurrent_workers() -> None:
    limiter = RunWorkerCapacityLimiter(limit=2)
    active_count = 0
    max_active_count = 0
    active_count_lock = asyncio.Lock()
    release = asyncio.Event()

    async def worker() -> None:
        nonlocal active_count, max_active_count
        async with active_count_lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
        await release.wait()
        async with active_count_lock:
            active_count -= 1

    tasks = tuple(
        asyncio.create_task(
            limiter.run(
                run_id=f"run-{index}",
                session_id=f"session-{index}",
                worker=worker,
            )
        )
        for index in range(5)
    )

    await asyncio.sleep(0)
    assert max_active_count == 2

    release.set()
    await asyncio.gather(*tasks)


def test_run_worker_capacity_default_allows_pressure_session_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RELAY_TEAMS_RUN_WORKER_ACTIVE_LIMIT", raising=False)

    assert run_worker_active_limit() == 32
