from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from threading import Lock
from typing import cast

import pytest

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs import event_stream as event_stream_module
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

    async def emit_run_events_async(
        self, events: tuple[RunEvent, ...]
    ) -> tuple[int, ...]:
        _ = events
        with self._lock:
            event_ids: list[int] = []
            for _event in events:
                self._next_event_id += 1
                event_ids.append(self._next_event_id)
            return tuple(event_ids)


class _ObservedRunStateRepository:
    def __init__(self, *, block_first_async_update: bool = False) -> None:
        self.active_updates = 0
        self.max_active_updates = 0
        self.applied_event_ids: list[int] = []
        self.async_update_entered = asyncio.Event()
        self.release_first_async_update = asyncio.Event()
        self._block_first_async_update = block_first_async_update
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
                if self._block_first_async_update:
                    await self.release_first_async_update.wait()
                else:
                    await asyncio.sleep(0)
            else:
                await asyncio.sleep(0)
            return self._state_record(event_id=event_id, event=event)
        finally:
            self._finish_update()

    async def apply_events_async(
        self,
        *,
        event_ids: tuple[int, ...],
        events: tuple[RunEvent, ...],
    ) -> tuple[RunStateRecord, ...]:
        return tuple(
            [
                await self.apply_event_async(event_id=event_id, event=event)
                for event_id, event in zip(event_ids, events, strict=True)
            ]
        )

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


def _event(event_type: RunEventType, *, run_id: str = "run-1") -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id=run_id,
        trace_id="trace-1",
        task_id="task-1",
        instance_id="instance-1",
        event_type=event_type,
        payload_json="{}",
    )


def _tool_result_event(tool_name: str) -> RunEvent:
    return _event(RunEventType.TOOL_RESULT).model_copy(
        update={
            "payload_json": (
                '{"tool_name": "'
                + tool_name
                + '", "tool_call_id": "call-1", "result": {"ok": true}}'
            )
        }
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

    published_event_ids = await asyncio.gather(
        hub.publish_async(_event(RunEventType.RUN_STARTED)),
        hub.publish_async(_event(RunEventType.MODEL_STEP_STARTED)),
    )

    first = queue.get_nowait()
    second = queue.get_nowait()

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1, 2]
    assert published_event_ids == [1, 2]
    assert first.event_id == 1
    assert second.event_id == 2


@pytest.mark.asyncio
async def test_run_event_hub_advances_state_projection_for_tool_result_batch() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    event_ids = await hub.publish_many_async(
        (_tool_result_event("spawn_subagent"), _tool_result_event("spawn_subagent"))
    )

    assert event_ids == (1, 2)
    assert run_state_repo.applied_event_ids == [1, 2]
    assert queue.get_nowait().event_type is RunEventType.TOOL_RESULT
    assert queue.get_nowait().event_type is RunEventType.TOOL_RESULT


@pytest.mark.asyncio
async def test_run_event_hub_deferred_lightweight_batch_skips_state_projection() -> (
    None
):
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    event_ids = await hub.publish_many_deferred_async(
        (_tool_result_event("read"), _tool_result_event("grep"))
    )
    delivered = (queue.get_nowait(), queue.get_nowait())

    assert event_ids == (1, 2)
    assert tuple(event.event_id for event in delivered) == (1, 2)
    assert run_state_repo.applied_event_ids == []


@pytest.mark.asyncio
async def test_run_event_hub_deferred_non_lightweight_batch_advances_state_projection() -> (
    None
):
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    event_ids = await hub.publish_many_deferred_async(
        (_tool_result_event("write"), _tool_result_event("read"))
    )
    delivered = (queue.get_nowait(), queue.get_nowait())

    assert event_ids == (1, 2)
    assert tuple(event.event_id for event in delivered) == (1, 2)
    assert run_state_repo.applied_event_ids == [1, 2]


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


@pytest.mark.asyncio
async def test_run_event_hub_wakes_subscriber_loop_from_thread_publish() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    event_id = await asyncio.to_thread(hub.publish, _event(RunEventType.RUN_COMPLETED))
    delivered = await asyncio.wait_for(queue.get(), timeout=1)

    assert event_id == 1
    assert delivered.event_type is RunEventType.RUN_COMPLETED
    assert delivered.event_id == 1


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


def test_run_event_hub_bounded_queue_drops_high_frequency_events_first() -> None:
    queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=1)
    queue.put_nowait(_event(RunEventType.TEXT_DELTA))

    RunEventHub._offer_event_on_owner_loop(
        queue,
        _event(RunEventType.THINKING_DELTA),
    )

    delivered = queue.get_nowait()
    assert delivered.event_type is RunEventType.TEXT_DELTA


def test_run_event_hub_bounded_queue_preserves_terminal_events() -> None:
    queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=1)
    queue.put_nowait(_event(RunEventType.TEXT_DELTA))

    RunEventHub._offer_event_on_owner_loop(
        queue,
        _event(RunEventType.RUN_COMPLETED),
    )

    delivered = queue.get_nowait()
    assert delivered.event_type is RunEventType.RUN_COMPLETED


def test_run_event_hub_bounded_queue_drops_only_droppable_events() -> None:
    queue: asyncio.Queue[RunEvent] = asyncio.Queue(maxsize=3)
    queue.put_nowait(
        _event(RunEventType.TOOL_RESULT).model_copy(update={"event_id": 1})
    )
    queue.put_nowait(_event(RunEventType.TEXT_DELTA).model_copy(update={"event_id": 2}))
    queue.put_nowait(
        _event(RunEventType.RUN_STARTED).model_copy(update={"event_id": 3})
    )

    RunEventHub._offer_event_on_owner_loop(
        queue,
        _event(RunEventType.RUN_COMPLETED).model_copy(update={"event_id": 4}),
    )

    delivered = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [event.event_id for event in delivered] == [1, 3, 4]
    assert [event.event_type for event in delivered] == [
        RunEventType.TOOL_RESULT,
        RunEventType.RUN_STARTED,
        RunEventType.RUN_COMPLETED,
    ]


def test_run_event_hub_subscriber_queue_keeps_non_droppable_overflow() -> None:
    queue = event_stream_module._RunEventSubscriberQueue(soft_maxsize=1)
    queue.put_nowait(
        _event(RunEventType.TOOL_RESULT).model_copy(update={"event_id": 1})
    )

    RunEventHub._offer_event_on_owner_loop(
        queue,
        _event(RunEventType.RUN_COMPLETED).model_copy(update={"event_id": 2}),
    )

    assert queue.qsize() == 2
    assert queue.get_nowait().event_type is RunEventType.TOOL_RESULT
    assert queue.get_nowait().event_type is RunEventType.RUN_COMPLETED


def test_lightweight_tool_result_batch_requires_supported_tool_results() -> None:
    assert event_stream_module._is_lightweight_tool_result_batch(()) is False
    assert (
        event_stream_module._is_lightweight_tool_result_batch(
            (_tool_result_event("read"), _tool_result_event("grep"))
        )
        is True
    )
    assert (
        event_stream_module._is_lightweight_tool_result_batch(
            (_event(RunEventType.TEXT_DELTA),)
        )
        is False
    )
    assert (
        event_stream_module._is_lightweight_tool_result_batch(
            (_event(RunEventType.TOOL_RESULT).model_copy(update={"payload_json": "{"}),)
        )
        is False
    )
    assert (
        event_stream_module._is_lightweight_tool_result_batch(
            (
                _event(RunEventType.TOOL_RESULT).model_copy(
                    update={"payload_json": '{"tool_name": 123}'}
                ),
            )
        )
        is False
    )
    assert (
        event_stream_module._is_lightweight_tool_result_batch(
            (_tool_result_event("write"),)
        )
        is False
    )


@pytest.mark.asyncio
async def test_run_event_hub_publish_async_finishes_projection_when_cancelled() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository(block_first_async_update=True)
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    task = asyncio.create_task(hub.publish_async(_event(RunEventType.RUN_STARTED)))
    await run_state_repo.async_update_entered.wait()
    task.cancel()
    run_state_repo.release_first_async_update.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    delivered = queue.get_nowait()

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1]
    assert delivered.event_id == 1


def test_run_event_hub_publish_returns_event_id() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository()
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )
    queue = hub.subscribe("run-1")

    event_id = hub.publish(_event(RunEventType.RUN_STARTED))
    delivered = queue.get_nowait()

    assert event_id == 1
    assert delivered.event_id == 1


@pytest.mark.asyncio
async def test_run_event_hub_allows_different_runs_to_publish_in_parallel() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository(block_first_async_update=True)
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )

    first_task = asyncio.create_task(
        hub.publish_async(_event(RunEventType.RUN_STARTED, run_id="run-1"))
    )
    await run_state_repo.async_update_entered.wait()
    second_event_id = await hub.publish_async(
        _event(RunEventType.RUN_STARTED, run_id="run-2")
    )
    run_state_repo.release_first_async_update.set()
    first_event_id = await first_task

    assert run_state_repo.max_active_updates == 2
    assert sorted([first_event_id, second_event_id]) == [1, 2]


@pytest.mark.asyncio
async def test_run_event_hub_serializes_mixed_sync_and_async_publishes() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository(block_first_async_update=True)
    hub = RunEventHub(
        event_log=cast(EventLog, event_log),
        run_state_repo=cast(RunStateRepository, run_state_repo),
    )

    async_task = asyncio.create_task(
        hub.publish_async(_event(RunEventType.RUN_STARTED))
    )
    await run_state_repo.async_update_entered.wait()
    run_state_repo.release_first_async_update.set()

    await asyncio.gather(
        async_task,
        asyncio.to_thread(hub.publish, _event(RunEventType.RUN_PAUSED)),
    )

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1, 2]


@pytest.mark.asyncio
async def test_run_event_hub_rejects_sync_publish_from_loop_when_async_locked() -> None:
    event_log = _SequencedEventLog()
    run_state_repo = _ObservedRunStateRepository(block_first_async_update=True)
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

    run_state_repo.release_first_async_update.set()
    await asyncio.wait_for(async_task, timeout=1)

    assert run_state_repo.max_active_updates == 1
    assert run_state_repo.applied_event_ids == [1]
