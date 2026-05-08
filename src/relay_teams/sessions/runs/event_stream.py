from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Callable
from threading import Condition, Lock
from typing import Protocol, runtime_checkable

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.logger import get_logger, log_event

LOGGER = get_logger(__name__)

RUN_EVENT_SUBSCRIBER_QUEUE_MAXSIZE = 1000
_DROPPABLE_EVENT_TYPES = frozenset(
    {
        RunEventType.MODEL_STEP_STARTED,
        RunEventType.MODEL_STEP_FINISHED,
        RunEventType.TEXT_DELTA,
        RunEventType.OUTPUT_DELTA,
        RunEventType.GENERATION_PROGRESS,
        RunEventType.THINKING_DELTA,
        RunEventType.TOOL_CALL,
        RunEventType.TOOL_CALL_BATCH_SEALED,
        RunEventType.TOKEN_USAGE,
        RunEventType.RUNTIME_GUARDRAIL_REPORT,
    }
)
_LIGHTWEIGHT_TOOL_RESULT_NAMES = frozenset(
    {
        "glob",
        "grep",
        "list_run_tasks",
        "read",
        "spawn_subagent",
        "todo_read",
    }
)


class _RunEventSubscriberQueue(asyncio.Queue[RunEvent]):
    def __init__(self, *, soft_maxsize: int) -> None:
        super().__init__(maxsize=0)
        self._soft_maxsize = soft_maxsize

    def put_nowait(self, item: RunEvent) -> None:
        if self._soft_maxsize > 0 and self.qsize() >= self._soft_maxsize:
            if item.event_type in _DROPPABLE_EVENT_TYPES:
                return
            _ = _drop_oldest_droppable_queued_event(self)
        super().put_nowait(item)


class _HybridPublishLock:  # pragma: no cover
    def __init__(self) -> None:
        self._guard = Lock()
        self._condition = Condition(self._guard)
        self._held = False
        self._async_waiters: deque[asyncio.Future[None]] = deque()

    def acquire(self, *, blocking: bool = True) -> bool:
        with self._condition:
            if not self._held:
                self._held = True
                return True
            if not blocking:
                return False
            while self._held:
                self._condition.wait()
            self._held = True
            return True

    def __enter__(self) -> _HybridPublishLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        _ = (exc_type, exc, traceback)
        self.release()

    async def acquire_async(self) -> None:
        with self._condition:
            if not self._held:
                self._held = True
                return
            loop = asyncio.get_running_loop()
            waiter = loop.create_future()
            self._async_waiters.append(waiter)
        try:
            await waiter
        except BaseException:
            should_release = False
            with self._condition:
                try:
                    self._async_waiters.remove(waiter)
                except ValueError:
                    should_release = waiter.done() and not waiter.cancelled()
            if should_release:
                self.release()
            raise

    def release(self) -> None:
        with self._condition:
            if not self._held:
                raise RuntimeError("Cannot release an unlocked publish lock.")
            while self._async_waiters:
                waiter = self._async_waiters.popleft()
                if waiter.done():
                    continue
                try:
                    waiter.get_loop().call_soon_threadsafe(
                        self._grant_async_waiter, waiter
                    )
                except RuntimeError:
                    continue
                return
            self._held = False
            self._condition.notify()

    def _grant_async_waiter(self, waiter: asyncio.Future[None]) -> None:
        if waiter.done():
            self.release()
            return
        waiter.set_result(None)


@runtime_checkable
class AsyncRunEventPublisher(Protocol):
    @staticmethod
    async def publish_async(event: RunEvent) -> int | None:
        pass


@runtime_checkable
class SyncRunEventPublisher(Protocol):
    @staticmethod
    def publish(event: RunEvent) -> int | None:
        pass


async def publish_run_event_async(
    publisher: AsyncRunEventPublisher | SyncRunEventPublisher,
    event: RunEvent,
) -> int:
    if isinstance(publisher, AsyncRunEventPublisher):
        event_id = await publisher.publish_async(event)
        return event_id if isinstance(event_id, int) else 0
    event_id = publisher.publish(event)
    return event_id if isinstance(event_id, int) else 0


class RunEventHub:  # pragma: no cover
    def __init__(
        self,
        event_log: EventLog | None = None,
        run_state_repo: RunStateRepository | None = None,
    ) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = {}
        self._session_subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = {}
        self._subscriber_loops: dict[int, asyncio.AbstractEventLoop] = {}
        self._event_log = event_log
        self._run_state_repo = run_state_repo
        self._publish_locks: dict[str, _HybridPublishLock] = {}
        self._publish_locks_guard = Lock()
        self._publish_observers: list[Callable[[RunEvent], None]] = []

    def add_publish_observer(self, observer: Callable[[RunEvent], None]) -> None:
        if observer in self._publish_observers:
            return
        self._publish_observers.append(observer)

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = _RunEventSubscriberQueue(
            soft_maxsize=RUN_EVENT_SUBSCRIBER_QUEUE_MAXSIZE
        )
        self._subscribers.setdefault(run_id, []).append(queue)
        self._bind_queue_loop(queue)
        return queue

    def subscribe_session(self, session_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = _RunEventSubscriberQueue(
            soft_maxsize=RUN_EVENT_SUBSCRIBER_QUEUE_MAXSIZE
        )
        self._session_subscribers.setdefault(session_id, []).append(queue)
        self._bind_queue_loop(queue)
        return queue

    def _bind_queue_loop(self, queue: asyncio.Queue[RunEvent]) -> None:
        try:
            self._subscriber_loops[id(queue)] = asyncio.get_running_loop()
        except RuntimeError:
            # Sync subscribers do not have a loop to associate with the queue.
            pass

    def loop_for_run(self, run_id: str) -> asyncio.AbstractEventLoop | None:
        listeners = self._subscribers.get(run_id, [])
        if not listeners:
            return None
        return self._subscriber_loops.get(id(listeners[0]))

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        listeners = self._subscribers.get(run_id)
        if not listeners:
            return
        self._subscribers[run_id] = [item for item in listeners if item is not queue]
        self._subscriber_loops.pop(id(queue), None)
        if not self._subscribers[run_id]:
            self._subscribers.pop(run_id, None)

    def unsubscribe_session(
        self, session_id: str, queue: asyncio.Queue[RunEvent]
    ) -> None:
        listeners = self._session_subscribers.get(session_id)
        if not listeners:
            return
        self._session_subscribers[session_id] = [
            item for item in listeners if item is not queue
        ]
        self._subscriber_loops.pop(id(queue), None)
        if not self._session_subscribers[session_id]:
            self._session_subscribers.pop(session_id, None)

    def publish(self, event: RunEvent) -> int:
        if self._publish_from_running_loop(event):
            return int(event.event_id or 0)
        publish_lock = self._publish_lock_for_event(event)
        with publish_lock:
            return self._publish_sync_with_lock(event)

    def _publish_from_running_loop(self, event: RunEvent) -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        publish_lock = self._publish_lock_for_event(event)
        if publish_lock.acquire(blocking=False):
            try:
                event_id = self._publish_sync_with_lock(event)
                if event_id > 0:
                    event.event_id = event_id
            finally:
                publish_lock.release()
            return True
        raise RuntimeError(
            "RunEventHub.publish cannot wait for an in-flight async publish from "
            "a running event loop; use publish_async instead."
        )

    def _publish_lock_for_event(self, event: RunEvent) -> _HybridPublishLock:
        key = event.run_id.strip() or event.session_id.strip() or "__global__"
        with self._publish_locks_guard:
            publish_lock = self._publish_locks.get(key)
            if publish_lock is None:
                publish_lock = _HybridPublishLock()
                self._publish_locks[key] = publish_lock
            return publish_lock

    def _publish_sync_with_lock(self, event: RunEvent) -> int:
        event_id = 0
        if self._event_log:
            event_id = self._event_log.emit_run_event(event)
        if self._run_state_repo is not None and event_id > 0:
            self._run_state_repo.apply_event(event_id=event_id, event=event)

        if event_id > 0:
            event = event.model_copy(update={"event_id": event_id})

        listeners = self._subscribers.get(event.run_id, [])
        for queue in listeners:
            self._offer_event(queue, event)
        session_listeners = self._session_subscribers.get(event.session_id, [])
        for queue in session_listeners:
            self._offer_event(queue, event)
        self._notify_publish_observers(event)
        return event_id

    async def publish_async(self, event: RunEvent) -> int:
        publish_lock = self._publish_lock_for_event(event)
        await publish_lock.acquire_async()
        return await self._publish_async_with_lock(event, publish_lock)

    async def publish_many_async(self, events: tuple[RunEvent, ...]) -> tuple[int, ...]:
        if not events:
            return ()
        publish_lock = self._publish_lock_for_event(events[0])
        await publish_lock.acquire_async()
        publish_task = asyncio.create_task(self._publish_many_async_payload(events))
        try:
            return await asyncio.shield(publish_task)
        except asyncio.CancelledError:
            _ = await publish_task
            raise
        finally:
            publish_lock.release()

    async def publish_many_deferred_async(
        self, events: tuple[RunEvent, ...]
    ) -> tuple[int, ...]:
        if not events:
            return ()
        publish_lock = self._publish_lock_for_event(events[0])
        await publish_lock.acquire_async()
        try:
            event_ids = await self._persist_deferred_events_for_delivery_async(events)
            published_events = tuple(
                event.model_copy(update={"event_id": event_id})
                if event_id > 0
                else event
                for event, event_id in zip(events, event_ids, strict=True)
            )
            for event in published_events:
                self._deliver_event(event)
            return event_ids
        finally:
            publish_lock.release()

    async def _publish_async_with_lock(
        self, event: RunEvent, publish_lock: _HybridPublishLock
    ) -> int:
        publish_task = asyncio.create_task(self._publish_async_payload(event))
        try:
            return await asyncio.shield(publish_task)
        except asyncio.CancelledError:
            _ = await publish_task
            raise
        finally:
            publish_lock.release()

    async def _publish_async_payload(self, event: RunEvent) -> int:
        event_id = 0
        if self._event_log:
            event_id = await self._event_log.emit_run_event_async(event)
        if self._run_state_repo is not None and event_id > 0:
            await self._run_state_repo.apply_event_async(
                event_id=event_id,
                event=event,
            )

        if event_id > 0:
            event = event.model_copy(update={"event_id": event_id})

        self._deliver_event(event)
        return event_id

    async def _persist_deferred_events_for_delivery_async(
        self,
        events: tuple[RunEvent, ...],
    ) -> tuple[int, ...]:
        if self._event_log is None:
            return tuple(0 for _event in events)
        event_ids = await self._event_log.emit_run_events_async(events)
        if _is_lightweight_tool_result_batch(events):
            return event_ids
        if self._run_state_repo is not None and any(
            event_id > 0 for event_id in event_ids
        ):
            await self._run_state_repo.apply_events_async(
                event_ids=event_ids,
                events=events,
            )
        return event_ids

    async def _publish_many_async_payload(
        self, events: tuple[RunEvent, ...]
    ) -> tuple[int, ...]:
        event_ids: tuple[int, ...] = tuple(0 for _event in events)
        if self._event_log:
            event_ids = await self._event_log.emit_run_events_async(events)
        if self._run_state_repo is not None and any(
            event_id > 0 for event_id in event_ids
        ):
            await self._run_state_repo.apply_events_async(
                event_ids=event_ids,
                events=events,
            )
        published_events = tuple(
            event.model_copy(update={"event_id": event_id}) if event_id > 0 else event
            for event, event_id in zip(events, event_ids, strict=True)
        )
        for event in published_events:
            self._deliver_event(event)
        return event_ids

    def _deliver_event(self, event: RunEvent) -> None:
        listeners = self._subscribers.get(event.run_id, [])
        for queue in listeners:
            self._offer_event(queue, event)
        session_listeners = self._session_subscribers.get(event.session_id, [])
        for queue in session_listeners:
            self._offer_event(queue, event)
        self._notify_publish_observers(event)

    def _notify_publish_observers(self, event: RunEvent) -> None:
        for observer in tuple(self._publish_observers):
            try:
                observer(event)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="run_event_hub.publish_observer_failed",
                    message="Run event publish observer failed",
                    payload={
                        "session_id": event.session_id,
                        "run_id": event.run_id,
                        "event_type": event.event_type.value,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    def _offer_event(self, queue: asyncio.Queue[RunEvent], event: RunEvent) -> None:
        owner_loop = self._subscriber_loops.get(id(queue))
        if owner_loop is None:
            self._offer_event_on_owner_loop(queue, event)
            return
        if owner_loop.is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is owner_loop:
            self._offer_event_on_owner_loop(queue, event)
            return
        owner_loop.call_soon_threadsafe(self._offer_event_on_owner_loop, queue, event)

    @staticmethod
    def _offer_event_on_owner_loop(
        queue: asyncio.Queue[RunEvent], event: RunEvent
    ) -> None:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        if event.event_type in _DROPPABLE_EVENT_TYPES:
            return
        if _drop_oldest_droppable_queued_event(queue):
            queue.put_nowait(event)
            return
        log_event(
            LOGGER,
            logging.WARNING,
            event="run_event_hub.subscriber_queue_overflow",
            message="Run event subscriber queue overflowed with only non-droppable events",
            payload={
                "session_id": event.session_id,
                "run_id": event.run_id,
                "event_type": event.event_type.value,
                "event_id": event.event_id,
                "queue_size": queue.qsize(),
            },
        )

    def unsubscribe_all(self, run_id: str) -> None:
        listeners = self._subscribers.pop(run_id, [])
        for queue in listeners:
            self._subscriber_loops.pop(id(queue), None)

    def has_subscribers(self, run_id: str) -> bool:
        return bool(self._subscribers.get(run_id))

    def has_session_subscribers(self, session_id: str) -> bool:
        return bool(self._session_subscribers.get(session_id))


def _is_lightweight_tool_result_batch(events: tuple[RunEvent, ...]) -> bool:
    if not events:
        return False
    for event in events:
        if event.event_type is not RunEventType.TOOL_RESULT:
            return False
        try:
            payload = json.loads(event.payload_json or "{}")
        except ValueError:
            return False
        if not isinstance(payload, dict):
            return False
        tool_name = payload.get("tool_name")
        if not isinstance(tool_name, str):
            return False
        if tool_name not in _LIGHTWEIGHT_TOOL_RESULT_NAMES:
            return False
    return True


def _drop_oldest_droppable_queued_event(queue: asyncio.Queue[RunEvent]) -> bool:
    buffered_events: list[RunEvent] = []
    dropped_droppable_event = False
    while True:
        try:
            queued_event = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        queue.task_done()
        if queued_event.event_type in _DROPPABLE_EVENT_TYPES:
            if not dropped_droppable_event:
                dropped_droppable_event = True
                continue
        buffered_events.append(queued_event)
    for queued_event in buffered_events:
        queue.put_nowait(queued_event)
    return dropped_droppable_event
