from __future__ import annotations

import asyncio

from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.run_state_repo import RunStateRepository


class RunEventHub:
    def __init__(
        self,
        event_log: EventLog | None = None,
        run_state_repo: RunStateRepository | None = None,
    ) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[RunEvent]]] = {}
        self._event_log = event_log
        self._run_state_repo = run_state_repo

    def subscribe(self, run_id: str) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[RunEvent]) -> None:
        listeners = self._subscribers.get(run_id)
        if not listeners:
            return
        self._subscribers[run_id] = [item for item in listeners if item is not queue]
        if not self._subscribers[run_id]:
            self._subscribers.pop(run_id, None)

    def publish(self, event: RunEvent) -> None:
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

    def unsubscribe_all(self, run_id: str) -> None:
        self._subscribers.pop(run_id, None)

    def has_subscribers(self, run_id: str) -> bool:
        return bool(self._subscribers.get(run_id))
