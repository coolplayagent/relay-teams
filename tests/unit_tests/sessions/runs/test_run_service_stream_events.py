# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from tests.unit_tests.sessions.runs.test_run_service_recovery import _build_manager


async def _collect_events(stream: AsyncIterable[RunEvent]) -> list[RunEvent]:
    return [event async for event in stream]


async def _next_event(stream: AsyncIterator[RunEvent]) -> RunEvent:
    return await stream.__anext__()


@pytest.mark.asyncio
async def test_stream_run_events_can_continue_after_run_paused(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stream_continue_after_pause.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")

    for event in (
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_STARTED,
            payload_json='{"session_id":"session-1"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_PAUSED,
            payload_json='{"error_message":"waiting for input"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed"}',
        ),
    ):
        manager._run_event_hub.publish(event)

    replayed = [
        event
        async for event in manager.stream_run_events(
            "run-existing",
            stop_on_pause=False,
        )
    ]

    assert [event.event_type for event in replayed] == [
        RunEventType.RUN_STARTED,
        RunEventType.RUN_PAUSED,
        RunEventType.RUN_COMPLETED,
    ]
    assert manager._run_event_hub.has_subscribers("run-existing") is False


@pytest.mark.asyncio
async def test_stream_run_events_pause_does_not_clear_other_subscribers(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stream_pause_keeps_other_subscribers.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")
    default_stream = manager.stream_run_events("run-existing")
    tolerant_stream = manager.stream_run_events(
        "run-existing",
        stop_on_pause=False,
    )
    default_task = asyncio.create_task(_collect_events(default_stream))
    tolerant_iter = tolerant_stream.__aiter__()
    tolerant_pause_task = asyncio.create_task(_next_event(tolerant_iter))
    await asyncio.sleep(0)

    manager._run_event_hub.publish(
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_PAUSED,
            payload_json='{"error_message":"waiting for input"}',
        )
    )

    default_events = await asyncio.wait_for(default_task, timeout=1)
    tolerant_pause = await asyncio.wait_for(tolerant_pause_task, timeout=1)
    assert [event.event_type for event in default_events] == [RunEventType.RUN_PAUSED]
    assert tolerant_pause.event_type == RunEventType.RUN_PAUSED
    assert manager._run_event_hub.has_subscribers("run-existing") is True

    manager._run_event_hub.publish(
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed"}',
        )
    )

    tolerant_completed = await asyncio.wait_for(_next_event(tolerant_iter), timeout=1)
    assert tolerant_completed.event_type == RunEventType.RUN_COMPLETED
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(_next_event(tolerant_iter), timeout=1)
    assert manager._run_event_hub.has_subscribers("run-existing") is False
