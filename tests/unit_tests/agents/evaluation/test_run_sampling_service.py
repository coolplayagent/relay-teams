# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.evaluation.run_sampling_service import (
    RunSamplingService,
    SampledRun,
    SamplingConfig,
)
from relay_teams.memory.models import (
    MemoryQueryResult,
)
from relay_teams.sessions.runs.run_state_models import RunStateRecord, RunStateStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_state(
    *,
    run_id: str,
    session_id: str,
    status: RunStateStatus = RunStateStatus.FAILED,
    updated_at: datetime | None = None,
    last_event_id: int = 10,
) -> RunStateRecord:
    if updated_at is None:
        updated_at = datetime.now(timezone.utc)
    return RunStateRecord(
        run_id=run_id,
        session_id=session_id,
        status=status,
        updated_at=updated_at,
        last_event_id=last_event_id,
    )


def _make_memory_bank_service(*, total_count: int = 0) -> MagicMock:
    svc = MagicMock()
    svc.list_entries_async = AsyncMock(
        return_value=MemoryQueryResult(
            items=(),
            total_count=total_count,
            offset=0,
            limit=100,
        )
    )
    return svc


def _make_sampling_service(
    *,
    run_states: tuple[RunStateRecord, ...] = (),
    config: SamplingConfig | None = None,
    memory_total: int = 0,
) -> RunSamplingService:
    event_log = MagicMock()
    event_log.list_run_states_async = AsyncMock(return_value=run_states)

    memory_bank_service = _make_memory_bank_service(total_count=memory_total)
    return RunSamplingService(
        event_log=event_log,
        memory_bank_service=memory_bank_service,
        config=config,
    )


def _recent_states(count: int, *, prefix: str = "run") -> tuple[RunStateRecord, ...]:
    now = datetime.now(timezone.utc)
    return tuple(
        _make_run_state(
            run_id=f"{prefix}-{i}",
            session_id=f"sess-{i}",
            updated_at=now - timedelta(hours=i),
        )
        for i in range(count)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_exact_count() -> None:
    """Pool of 100 runs -> sample 50."""
    states = _recent_states(100)
    svc = _make_sampling_service(
        run_states=states, config=SamplingConfig(sample_size=50)
    )

    result = await svc.sample_runs()

    assert len(result) == 50
    for r in result:
        assert isinstance(r, SampledRun)


@pytest.mark.asyncio
async def test_sample_insufficient_pool() -> None:
    """Pool of 20 runs -> all 20 returned."""
    states = _recent_states(20)
    svc = _make_sampling_service(
        run_states=states, config=SamplingConfig(sample_size=50)
    )

    result = await svc.sample_runs()

    assert len(result) == 20
    for r in result:
        assert isinstance(r, SampledRun)


@pytest.mark.asyncio
async def test_sample_excludes_classified() -> None:
    """Runs with existing FAILURE_MODE entries are excluded."""
    now = datetime.now(timezone.utc)
    states: tuple[RunStateRecord, ...] = tuple(
        _make_run_state(
            run_id=f"run-{i}",
            session_id="sess-shared",
            updated_at=now,
        )
        for i in range(10)
    )

    # memory_bank_service returns total_count > 0, so the session_id is excluded
    svc = _make_sampling_service(
        run_states=states,
        config=SamplingConfig(sample_size=5),
        memory_total=1,  # Simulates existing FAILURE_MODE entries
    )

    result = await svc.sample_runs()

    # All runs share the same session, and that session has FAILURE_MODE entries
    assert len(result) == 0


@pytest.mark.asyncio
async def test_sample_seed_reproducibility() -> None:
    """Same seed -> same results."""
    states = _recent_states(50)
    config = SamplingConfig(sample_size=10, seed=42)

    svc1 = _make_sampling_service(run_states=states, config=config)
    svc2 = _make_sampling_service(run_states=states, config=config)

    result1 = await svc1.sample_runs()
    result2 = await svc2.sample_runs()

    ids1 = tuple(r.run_id for r in result1)
    ids2 = tuple(r.run_id for r in result2)
    assert ids1 == ids2


@pytest.mark.asyncio
async def test_sample_only_failed() -> None:
    """include_only_failed=True -> only failed runs returned."""
    now = datetime.now(timezone.utc)
    states: tuple[RunStateRecord, ...] = (
        _make_run_state(
            run_id="run-fail",
            session_id="sess-f",
            status=RunStateStatus.FAILED,
            updated_at=now,
        ),
        _make_run_state(
            run_id="run-ok",
            session_id="sess-o",
            status=RunStateStatus.COMPLETED,
            updated_at=now,
        ),
        _make_run_state(
            run_id="run-fail2",
            session_id="sess-f2",
            status=RunStateStatus.FAILED,
            updated_at=now,
        ),
    )

    svc = _make_sampling_service(
        run_states=states,
        config=SamplingConfig(sample_size=10, include_only_failed=True),
    )

    result = await svc.sample_runs()

    run_ids = {r.run_id for r in result}
    assert "run-fail" in run_ids
    assert "run-fail2" in run_ids
    assert "run-ok" not in run_ids


@pytest.mark.asyncio
async def test_sample_filter_workspace() -> None:
    """Filter by workspace_id -> only matching runs."""
    now = datetime.now(timezone.utc)
    states: tuple[RunStateRecord, ...] = (
        _make_run_state(run_id="run-a", session_id="ws-alpha", updated_at=now),
        _make_run_state(run_id="run-b", session_id="ws-beta", updated_at=now),
        _make_run_state(run_id="run-c", session_id="ws-alpha", updated_at=now),
    )

    svc = _make_sampling_service(
        run_states=states,
        config=SamplingConfig(sample_size=10, workspace_ids=("ws-alpha",)),
    )

    result = await svc.sample_runs()

    run_ids = {r.run_id for r in result}
    assert "run-a" in run_ids
    assert "run-c" in run_ids
    assert "run-b" not in run_ids


@pytest.mark.asyncio
async def test_sample_filter_role() -> None:
    """Filter by role_id -> the service currently doesn't filter by role_id
    at the sampling stage (RunStateRecord has no role_id), so all runs pass."""
    states = _recent_states(20)

    svc = _make_sampling_service(
        run_states=states,
        config=SamplingConfig(sample_size=5, role_ids=("role-x",)),
    )

    result = await svc.sample_runs()

    # role filtering is not implemented at sampling stage, so all states pass
    assert len(result) == 5


@pytest.mark.asyncio
async def test_sample_empty_pool() -> None:
    """No runs in window -> empty tuple returned."""
    svc = _make_sampling_service(run_states=(), config=SamplingConfig(sample_size=50))

    result = await svc.sample_runs()

    assert len(result) == 0
    assert isinstance(result, tuple)
