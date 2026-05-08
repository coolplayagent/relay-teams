# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid
from typing import Protocol

from relay_teams.agent_runtimes.bus_models import (
    A2aBusMessage,
    A2aSubscription,
)


class _A2aBusProtocol(Protocol):
    """Minimal protocol for the A2A bus methods used by tools."""

    async def publish(self, message: A2aBusMessage) -> None:
        pass  # pragma: no cover

    async def subscribe(self, sub: A2aSubscription) -> None:
        pass  # pragma: no cover


def _generate_message_id() -> str:
    return f"a2a-{uuid.uuid4().hex[:12]}"


async def send_a2a_message(
    *,
    topic: str,
    content: str,
    target_role_id: str | None = None,
    payload_json: str = "{}",
    sender_role_id: str,
    sender_instance_id: str,
    a2a_bus: _A2aBusProtocol,
) -> dict[str, str | bool]:
    """Publish an A2A message onto the run-level bus.

    Called by the Coordinator or an Agent via its tool context.
    """
    message_id = _generate_message_id()
    message = A2aBusMessage(
        message_id=message_id,
        sender_role_id=sender_role_id,
        sender_instance_id=sender_instance_id,
        topic=topic,
        content=content,
        payload_json=payload_json,
        target_role_id=target_role_id,
    )
    await a2a_bus.publish(message)
    return {"published": True, "message_id": message_id}


async def subscribe_a2a_topic(
    *,
    topic: str,
    role_id: str | None = None,
    sender_role_id: str,
    sender_instance_id: str,
    a2a_bus: _A2aBusProtocol,
) -> dict[str, str | bool]:
    """Subscribe to an A2A topic for the current role."""
    effective_role_id = role_id if role_id else sender_role_id
    sub = A2aSubscription(
        role_id=effective_role_id,
        instance_id=sender_instance_id,
        topic=topic,
        receive_broadcast=True,
    )
    await a2a_bus.subscribe(sub)
    return {"subscribed": True, "topic": topic}
