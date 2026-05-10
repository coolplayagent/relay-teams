# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_state_models import (
    RunSnapshotRecord,
    RunStateStatus,
)
from relay_teams.sessions.runs.run_state_repo import RunStateRepository


@pytest.mark.asyncio
async def test_run_state_repository_async_methods_share_persisted_state(
    tmp_path: Path,
) -> None:
    repository = RunStateRepository(tmp_path / "run_state_repo_async.db")
    event = RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="instance-1",
        event_type=RunEventType.RUN_STARTED,
        payload_json="{}",
    )

    try:
        state = await repository.apply_event_async(event_id=1, event=event)
        by_session = await repository.list_by_session_async("session-1")
        snapshot = await repository.get_latest_snapshot_async("run-1")
        recoverable = await repository.list_recoverable_async()
        await repository.delete_async("run-1")
        deleted = await repository.get_run_state_async("run-1")
    finally:
        await repository.close_async()

    assert state.status == RunStateStatus.RUNNING
    assert tuple(item.run_id for item in by_session) == ("run-1",)
    assert snapshot is not None
    assert snapshot.checkpoint_event_id == 1
    assert tuple(item.run_id for item in recoverable) == ("run-1",)
    assert deleted is None


@pytest.mark.asyncio
async def test_run_state_async_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = RunStateRepository(tmp_path / "run_state_repo_no_reinit.db")
    event = RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="instance-1",
        event_type=RunEventType.RUN_STARTED,
        payload_json="{}",
    )

    async def _fail_init() -> None:
        raise AssertionError("async schema init should not run on hot paths")

    monkeypatch.setattr(repository, "_init_tables_async", _fail_init)

    try:
        state = await repository.apply_event_async(event_id=1, event=event)
        await repository.upsert_async(state)
        loaded = await repository.get_run_state_async("run-1")
        by_session = await repository.list_by_session_async("session-1")
        snapshot = await repository.get_latest_snapshot_async("run-1")
        recoverable = await repository.list_recoverable_async()
        await repository.delete_async("run-1")
        deleted = await repository.get_run_state_async("run-1")
    finally:
        await repository.close_async()

    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert tuple(item.run_id for item in by_session) == ("run-1",)
    assert snapshot is not None
    assert tuple(item.run_id for item in recoverable) == ("run-1",)
    assert deleted is None


@pytest.mark.asyncio
async def test_run_state_async_schema_init_and_snapshot_upserts(
    tmp_path: Path,
) -> None:
    repository = RunStateRepository(tmp_path / "run_state_repo_schema_async.db")
    event = RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        instance_id="instance-1",
        event_type=RunEventType.RUN_STARTED,
        payload_json="{}",
    )

    try:
        await repository._init_tables_async()
        missing_snapshot = await repository.get_latest_snapshot_async("missing")
        state = await repository.apply_event_async(event_id=1, event=event)
        sync_snapshot = RunSnapshotRecord(
            run_id=state.run_id,
            session_id=state.session_id,
            checkpoint_event_id=2,
            state=state,
            created_at=state.updated_at,
        )
        repository._upsert_snapshot(sync_snapshot)
        async_snapshot = RunSnapshotRecord(
            run_id=state.run_id,
            session_id=state.session_id,
            checkpoint_event_id=3,
            state=state,
            created_at=state.updated_at,
        )
        await repository._upsert_snapshot_async(async_snapshot)
        latest_snapshot = await repository.get_latest_snapshot_async("run-1")
        recoverable = repository.list_recoverable()
    finally:
        await repository.close_async()

    assert missing_snapshot is None
    assert latest_snapshot is not None
    assert latest_snapshot.checkpoint_event_id == 3
    assert tuple(item.run_id for item in recoverable) == ("run-1",)


@pytest.mark.asyncio
async def test_run_state_applies_event_batch_and_coalesces_tool_snapshots(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_state_repo_batch.db"
    repository = RunStateRepository(db_path)
    events = (
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.TOOL_RESULT,
            payload_json='{"tool_name": "read"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.TOOL_RESULT,
            payload_json='{"tool_name": "grep"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json="{}",
        ),
    )

    try:
        state = await repository.apply_events_async(
            event_ids=(1, 2, 3, 4),
            events=events,
        )
        latest_snapshot = await repository.get_latest_snapshot_async("run-1")
    finally:
        await repository.close_async()

    with sqlite3.connect(db_path) as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) FROM run_snapshots").fetchone()

    assert state is not None
    assert state.status == RunStateStatus.COMPLETED
    assert state.last_event_id == 4
    assert latest_snapshot is not None
    assert latest_snapshot.checkpoint_event_id == 4
    assert snapshot_count is not None
    assert snapshot_count[0] == 3


@pytest.mark.asyncio
async def test_run_state_apply_events_async_handles_mixed_runs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_state_repo_batch_mixed_runs.db"
    repository = RunStateRepository(db_path)
    events = (
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-2",
            trace_id="run-2",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.TOOL_RESULT,
            payload_json='{"tool_name": "read"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-2",
            trace_id="run-2",
            event_type=RunEventType.TOOL_RESULT,
            payload_json='{"tool_name": "grep"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-1",
            trace_id="run-1",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json="{}",
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-2",
            trace_id="run-2",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json="{}",
        ),
    )

    try:
        state = await repository.apply_events_async(
            event_ids=(1, 2, 3, 4, 5, 6),
            events=events,
        )
        run_1 = await repository.get_run_state_async("run-1")
        run_2 = await repository.get_run_state_async("run-2")
    finally:
        await repository.close_async()

    with sqlite3.connect(db_path) as conn:
        snapshot_rows = conn.execute(
            "SELECT run_id, checkpoint_event_id FROM run_snapshots "
            "ORDER BY checkpoint_event_id"
        ).fetchall()

    assert state is not None
    assert state.run_id == "run-2"
    assert run_1 is not None
    assert run_1.run_id == "run-1"
    assert run_1.status == RunStateStatus.COMPLETED
    assert run_1.last_event_id == 5
    assert run_2 is not None
    assert run_2.run_id == "run-2"
    assert run_2.status == RunStateStatus.COMPLETED
    assert run_2.last_event_id == 6
    assert snapshot_rows == [
        ("run-1", 1),
        ("run-2", 2),
        ("run-1", 3),
        ("run-2", 4),
        ("run-1", 5),
        ("run-2", 6),
    ]


@pytest.mark.asyncio
async def test_run_state_apply_events_async_returns_none_for_empty_batch(
    tmp_path: Path,
) -> None:
    repository = RunStateRepository(tmp_path / "run_state_repo_empty_batch.db")
    try:
        state = await repository.apply_events_async(event_ids=(), events=())
    finally:
        await repository.close_async()

    assert state is None
