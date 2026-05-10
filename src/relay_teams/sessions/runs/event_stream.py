from __future__ import annotations

import asyncio
from threading import Lock
from typing import Protocol, runtime_checkable

from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_state_repo import RunStateRepository


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


class RunEventHub:
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
        self._publish_lock = Lock()

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        self._bind_queue_loop(queue)
        return queue

    def subscribe_session(self, session_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
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
        with self._publish_lock:
            return self._publish_sync_with_lock(event)

    def _publish_from_running_loop(self, event: RunEvent) -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self._publish_lock.acquire(blocking=False):
            try:
                event_id = self._publish_sync_with_lock(event)
                if event_id > 0:
                    event.event_id = event_id
            finally:
                self._publish_lock.release()
            return True
        raise RuntimeError(
            "RunEventHub.publish cannot wait for an in-flight async publish from "
            "a running event loop; use publish_async instead."
        )

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
            queue.put_nowait(event)
        session_listeners = self._session_subscribers.get(event.session_id, [])
        for queue in session_listeners:
            queue.put_nowait(event)
        return event_id

    async def publish_async(self, event: RunEvent) -> int:
        await self._acquire_publish_lock_async()
        task = asyncio.create_task(self._publish_async_with_lock(event))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            _ = await task
            raise

    async def publish_many_async(self, events: tuple[RunEvent, ...]) -> tuple[int, ...]:
        if not events:
            return ()
        await self._acquire_publish_lock_async()
        task = asyncio.create_task(self._publish_many_async_with_lock(events))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            _ = await task
            raise

    async def _publish_async_with_lock(self, event: RunEvent) -> int:
        try:
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

            listeners = self._subscribers.get(event.run_id, [])
            for queue in listeners:
                queue.put_nowait(event)
            session_listeners = self._session_subscribers.get(event.session_id, [])
            for queue in session_listeners:
                queue.put_nowait(event)
            return event_id
        finally:
            self._publish_lock.release()

    async def _publish_many_async_with_lock(
        self, events: tuple[RunEvent, ...]
    ) -> tuple[int, ...]:
        try:
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
                event.model_copy(update={"event_id": event_id})
                if event_id > 0
                else event
                for event, event_id in zip(events, event_ids, strict=True)
            )
            for event in published_events:
                listeners = self._subscribers.get(event.run_id, [])
                for queue in listeners:
                    queue.put_nowait(event)
                session_listeners = self._session_subscribers.get(event.session_id, [])
                for queue in session_listeners:
                    queue.put_nowait(event)
            return event_ids
        finally:
            self._publish_lock.release()

    async def _acquire_publish_lock_async(self) -> None:
        while not self._publish_lock.acquire(blocking=False):
            await asyncio.sleep(0.001)

    def unsubscribe_all(self, run_id: str) -> None:
        listeners = self._subscribers.pop(run_id, [])
        for queue in listeners:
            self._subscriber_loops.pop(id(queue), None)

    def has_subscribers(self, run_id: str) -> bool:
        return bool(self._subscribers.get(run_id))

    def has_session_subscribers(self, session_id: str) -> bool:
        return bool(self._session_subscribers.get(session_id))
