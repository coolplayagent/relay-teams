from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock
from typing import cast

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_state_models import RunStateRecord
from relay_teams.sessions.runs.run_state_repo import RunStateRepository


class _SequencedEventLog:
    def __init__(self) -> None:
        self._next_event_id = 0
        self._lock = Lock()

    def emit_run_event(self, event: RunEvent) -> int:
        _ = event
        with self._lock:
            self._next_event_id += 1
            return self._next_event_id

    async def emit_run_event_async(self, event: RunEvent) -> int:
        _ = event
        with self._lock:
            self._next_event_id += 1
            return self._next_event_id


class _ObservedRunStateRepository:
    def __init__(self) -> None:
        self.active_updates = 0
        self.max_active_updates = 0
        self.applied_event_ids: list[int] = []
        self.async_update_entered = asyncio.Event()
        self._lock = Lock()

    def apply_event(self, *, event_id: int, event: RunEvent) -> RunStateRecord:
        self._begin_update(event_id)
        try:
            return self._state_record(event_id=event_id, event=event)
        finally:
            self._finish_update()

    async def apply_event_async(
        self, *, event_id: int, event: RunEvent
    ) -> RunStateRecord:
        self._begin_update(event_id)
        try:
            if event_id == 1:
                self.async_update_entered.set()
                await asyncio.sleep(0.01)
            else:
                await asyncio.sleep(0)
            return self._state_record(event_id=event_id, event=event)
        finally:
            self._finish_update()

    def _begin_update(self, event_id: int) -> None:
        with self._lock:
            self.active_updates += 1
            self.max_active_updates = max(self.max_active_updates, self.active_updates)
            self.applied_event_ids.append(event_id)

    def _finish_update(self) -> None:
        with self._lock:
            self.active_updates -= 1

    def _state_record(self, *, event_id: int, event: RunEvent) -> RunStateRecord:
        return RunStateRecord(
            run_id=event.run_id,
            session_id=event.session_id,
            last_event_id=event_id,
            checkpoint_event_id=event_id,
            updated_at=datetime.now(tz=timezone.utc),
        )


def _event(event_type: RunEventType) -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        instance_id="instance-1",
        event_type=event_type,
        payload_json="{}",
    )


@pytest.mark.asyncio
async def test_run_event_hub_publish_async_serializes_state_updates() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    await asyncio.gather(
        hub.publish_async(_event(RunEventType.RUN_STARTED)),
        hub.publish_async(_event(RunEventType.MODEL_STEP_STARTED)),
    )

    first = queue.get_nowait()
    second = queue.get_nowait()

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1, 2]
    assert first.event_id == 1
    assert second.event_id == 2


@pytest.mark.asyncio
async def test_run_event_hub_publishes_to_session_subscribers() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe_session("session-1")

    await hub.publish_async(_event(RunEventType.RUN_STARTED))

    delivered = queue.get_nowait()

    assert hub.has_session_subscribers("session-1") is True
    assert delivered.session_id == "session-1"
    assert delivered.run_id == "run-1"
    assert delivered.event_id == 1

    hub.unsubscribe_session("session-1", queue)

    assert hub.has_session_subscribers("session-1") is False


def test_run_event_hub_sync_publish_delivers_to_session_subscribers() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe_session("session-1")

    hub.publish(_event(RunEventType.RUN_STARTED))

    delivered = queue.get_nowait()
    assert delivered.run_id == "run-1"
    assert delivered.event_id == 1


def test_run_event_hub_unsubscribe_session_ignores_unknown_queue() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue: asyncio.Queue[RunEvent] = asyncio.Queue()

    hub.unsubscribe_session("session-1", queue)

    assert hub.has_session_subscribers("session-1") is False


@pytest.mark.asyncio
async def test_run_event_hub_publish_async_finishes_projection_when_cancelled() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    task = asyncio.create_task(hub.publish_async(_event(RunEventType.RUN_STARTED)))
    await run_state_repo.async_update_entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    delivered = queue.get_nowait()

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1]
    assert delivered.event_id == 1


@pytest.mark.asyncio
async def test_run_event_hub_serializes_mixed_sync_and_async_publishes() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )

    async_task = asyncio.create_task(
        hub.publish_async(_event(RunEventType.RUN_STARTED))
    )
    await run_state_repo.async_update_entered.wait()

    await asyncio.gather(
        async_task,
        asyncio.to_thread(hub.publish, _event(RunEventType.RUN_PAUSED)),
    )

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1, 2]


@pytest.mark.asyncio
async def test_run_event_hub_rejects_sync_publish_from_loop_when_async_locked() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )

    async_task = asyncio.create_task(
        hub.publish_async(_event(RunEventType.RUN_STARTED))
    )
    await run_state_repo.async_update_entered.wait()

    with pytest.raises(RuntimeError, match="use publish_async"):
        hub.publish(_event(RunEventType.RUN_PAUSED))

    await asyncio.wait_for(async_task, timeout=1)

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1]
