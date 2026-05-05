# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Protocol

from relay_teams.agents.orchestration.a2a_bus_models import (
    A2aBusMessage,
    A2aBusState,
    A2aSubscription,
)
from relay_teams.agents.tasks.events import EventEnvelope, EventType
from relay_teams.logger import get_logger

LOGGER = get_logger(__name__)

_DEFAULT_MAX_HISTORY: int = 500


class _BusEventLogProtocol(Protocol):
    """Protocol for event log: only requires async emit."""

    async def emit_async(self, event: EventEnvelope) -> None: ...


class A2ABus:
    """Run-scoped internal A2A event bus.

    Lifecycle: bound to a single Run; destroyed when the Run ends.
    Storage: in-memory receive queues + local history list (for post-hoc queries).

    Does NOT change Coordinator orchestration authority:
      - Peer agents communicate via the bus, but task delegation remains
        under the Coordinator's exclusive control.
      - A2A messages do not affect the task dependency graph; they only
        carry context / discovery / feedback information.
    """

    def __init__(
        self,
        *,
        run_id: str,
        event_log: _BusEventLogProtocol | None = None,
    ) -> None:
        self._run_id = run_id
        self._event_log = event_log
        self._subscriptions: dict[str, list[A2aSubscription]] = defaultdict(list)
        self._receive_queues: dict[str, list[A2aBusMessage]] = defaultdict(list)
        self._message_history: list[A2aBusMessage] = []
        self._max_history: int = _DEFAULT_MAX_HISTORY

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def publish(self, message: A2aBusMessage) -> None:
        """Publish a message onto the bus.

        * Appends to the history (evicting oldest when over capacity).
        * Writes a ``A2A_MESSAGE_PUBLISHED`` event via *event_log*.
        * Delivers to matching subscribers.
        """
        # ---- history ----
        self._message_history.append(message)
        while len(self._message_history) > self._max_history:
            self._message_history.pop(0)

        # ---- event log ----
        await self._write_event(
            EventType.A2A_MESSAGE_PUBLISHED,
            message,
        )

        # ---- delivery ----
        topic_subs = self._subscriptions.get(message.topic, [])
        for sub in topic_subs:
            if message.target_role_id is not None:
                if sub.role_id != message.target_role_id:
                    continue
            elif not sub.receive_broadcast:
                continue

            self._receive_queues[sub.role_id].append(message)

            await self._write_event(
                EventType.A2A_MESSAGE_DELIVERED,
                message,
            )

    async def subscribe(self, sub: A2aSubscription) -> None:
        """Register a subscriber for a topic."""
        self._subscriptions[sub.topic].append(sub)

    async def unsubscribe(self, role_id: str, instance_id: str, topic: str) -> None:
        """Remove a subscription."""
        topic_subs = self._subscriptions.get(topic, [])
        self._subscriptions[topic] = [
            s
            for s in topic_subs
            if not (s.role_id == role_id and s.instance_id == instance_id)
        ]
        if not self._subscriptions[topic]:
            self._subscriptions.pop(topic, None)

    async def receive(
        self, role_id: str, *, topic: str | None = None
    ) -> list[A2aBusMessage]:
        """Dequeue and return messages for *role_id*, optionally filtered by topic."""
        queue = self._receive_queues.get(role_id, [])
        result: list[A2aBusMessage] = []
        remaining: list[A2aBusMessage] = []
        for msg in queue:
            if topic is not None and msg.topic != topic:
                remaining.append(msg)
            else:
                result.append(msg)
        self._receive_queues[role_id] = remaining
        return result

    def get_history(
        self,
        *,
        topic: str | None = None,
        role_id: str | None = None,
    ) -> list[A2aBusMessage]:
        """Return filtered message history (non-destructive)."""
        result = self._message_history
        if topic is not None:
            result = [m for m in result if m.topic == topic]
        if role_id is not None:
            result = [
                m
                for m in result
                if m.sender_role_id == role_id or m.target_role_id == role_id
            ]
        return list(result)

    def snapshot(self) -> A2aBusState:
        """Return current bus state snapshot."""
        return A2aBusState(
            run_id=self._run_id,
            message_count=len(self._message_history),
            subscription_count=sum(len(subs) for subs in self._subscriptions.values()),
            active_topics=tuple(self._subscriptions.keys()),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _write_event(
        self,
        event_type: EventType,
        message: A2aBusMessage,
    ) -> None:
        if self._event_log is None:
            return
        import json

        payload: dict[str, object] = {
            "message_id": message.message_id,
            "sender_role_id": message.sender_role_id,
            "sender_instance_id": message.sender_instance_id,
            "topic": message.topic,
            "content": message.content[:500],
            "target_role_id": message.target_role_id,
            "source_task_id": message.source_task_id,
        }

        envelope = EventEnvelope(
            event_type=event_type,
            trace_id=self._run_id,
            session_id=self._run_id,
            payload_json=json.dumps(payload),
        )
        try:
            await self._event_log.emit_async(envelope)
        except (OSError, RuntimeError) as exc:
            LOGGER.warning(
                "failed to write A2A event %s: %s",
                event_type.value,
                exc,
            )


def _generate_message_id() -> str:
    return f"a2a-{uuid.uuid4().hex[:12]}"
