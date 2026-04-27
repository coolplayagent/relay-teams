# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_models import RunEvent


@pytest.mark.asyncio
async def test_event_log_async_methods_share_persisted_state(tmp_path: Path) -> None:
    event_log = EventLog(tmp_path / "event_log_async.db")
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
        event_id = await event_log.emit_run_event_async(event)
        by_trace = await event_log.list_by_trace_with_ids_async("run-1")
        by_session = await event_log.list_by_session_with_ids_async("session-1")
        run_state = await event_log.get_run_state_async("run-1")
    finally:
        await event_log.close_async()

    assert event_id > 0
    assert tuple(item["id"] for item in by_trace) == (event_id,)
    assert tuple(item["id"] for item in by_session) == (event_id,)
    assert run_state is not None
    assert run_state.run_id == "run-1"
    assert run_state.checkpoint_event_id == event_id


@pytest.mark.asyncio
async def test_event_log_async_hot_paths_do_not_reinitialize_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_log = EventLog(tmp_path / "event_log_async_no_reinit.db")
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
        raise AssertionError("async schema init must not run on hot paths")

    monkeypatch.setattr(event_log, "_init_tables_async", _fail_init)

    try:
        event_id = await event_log.emit_run_event_async(event)
        by_trace = await event_log.list_by_trace_with_ids_async("run-1")
        await event_log.delete_by_trace_async("run-1")
    finally:
        await event_log.close_async()

    assert event_id > 0
    assert tuple(item["id"] for item in by_trace) == (event_id,)


@pytest.mark.asyncio
async def test_event_log_lists_session_events_after_id_and_filters_subagent_runs(
    tmp_path: Path,
) -> None:
    event_log = EventLog(tmp_path / "event_log_session_after_id.db")
    first_id = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-main",
            trace_id="run-main",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        )
    )
    subagent_id = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="subagent_run_1",
            trace_id="subagent_run_1",
            event_type=RunEventType.MODEL_STEP_STARTED,
            payload_json="{}",
        )
    )
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-2",
            run_id="subagent_run_2",
            trace_id="subagent_run_2",
            event_type=RunEventType.MODEL_STEP_STARTED,
            payload_json="{}",
        )
    )

    session_rows = event_log.list_by_session_after_id("session-1", first_id)
    subagent_rows = event_log.list_subagent_run_events_by_session_after_id(
        "session-1",
        0,
    )
    async_session_rows = await event_log.list_by_session_after_id_async(
        "session-1",
        first_id,
    )
    async_subagent_rows = (
        await event_log.list_subagent_run_events_by_session_after_id_async(
            "session-1",
            0,
        )
    )
    await event_log.close_async()

    assert tuple(row["id"] for row in session_rows) == (subagent_id,)
    assert tuple(row["id"] for row in async_session_rows) == (subagent_id,)
    assert tuple(row["id"] for row in subagent_rows) == (subagent_id,)
    assert tuple(row["id"] for row in async_subagent_rows) == (subagent_id,)
