from __future__ import annotations

import asyncio
from threading import Lock
from typing import Protocol, runtime_checkable

from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_state_repo import RunStateRepository


@runtime_checkable
class AsyncRunEventPublisher(Protocol):
    async def publish_async(self, event: RunEvent) -> None:
        pass


@runtime_checkable
class SyncRunEventPublisher(Protocol):
    def publish(self, event: RunEvent) -> None:
        pass


async def publish_run_event_async(
    publisher: AsyncRunEventPublisher | SyncRunEventPublisher,
    event: RunEvent,
) -> None:
    if isinstance(publisher, AsyncRunEventPublisher):
        await publisher.publish_async(event)
        return
    publisher.publish(event)


class RunEventHub:
    def __init__(
        self,
        event_log: EventLog | None = None,
        run_state_repo: RunStateRepository | None = None,
    ) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = {}
        self._subscriber_loops: dict[int, asyncio.AbstractEventLoop] = {}
        self._event_log = event_log
        self._run_state_repo = run_state_repo
        self._publish_lock = Lock()

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        try:
            self._subscriber_loops[id(queue)] = asyncio.get_running_loop()
        except RuntimeError:
            # Sync subscribers do not have a loop to associate with the queue.
            pass
        return queue

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

    def publish(self, event: RunEvent) -> None:
        if self._publish_from_running_loop(event):
            return
        with self._publish_lock:
            self._publish_sync_with_lock(event)

    def _publish_from_running_loop(self, event: RunEvent) -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        if self._publish_lock.acquire(blocking=False):
            try:
                self._publish_sync_with_lock(event)
            finally:
                self._publish_lock.release()
            return True
        raise RuntimeError(
            "RunEventHub.publish cannot wait for an in-flight async publish from "
            "a running event loop; use publish_async instead."
        )

    def _publish_sync_with_lock(self, event: RunEvent) -> None:
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

    async def publish_async(self, event: RunEvent) -> None:
        await self._acquire_publish_lock_async()
        task = asyncio.create_task(self._publish_async_with_lock(event))
        try:
            _ = await asyncio.shield(task)
        except asyncio.CancelledError:
            _ = await task
            raise

    async def _publish_async_with_lock(self, event: RunEvent) -> None:
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
