# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import cast

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.models import RunEvent
from agent_teams.notifications import (
    NotificationConfig,
    NotificationChannel,
    NotificationContext,
    NotificationRule,
    NotificationService,
    NotificationType,
    default_notification_config,
)
from agent_teams.sessions.runs.event_stream import RunEventHub


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


def test_emit_publishes_notification_requested_event() -> None:
    hub = _FakeRunEventHub()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
    )
    emitted = service.emit(
        notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
        title="Approval Required",
        body="spec_coder requests approval for write.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            tool_call_id="toolcall-1",
            tool_name="write",
        ),
    )

    assert emitted is True
    assert len(hub.events) == 1
    event = hub.events[0]
    assert event.event_type == RunEventType.NOTIFICATION_REQUESTED
    payload = json.loads(event.payload_json)
    assert payload["notification_type"] == "tool_approval_requested"
    assert payload["channels"] == ["browser", "toast"]


def test_emit_returns_false_when_type_is_disabled() -> None:
    hub = _FakeRunEventHub()
    config = NotificationConfig(
        tool_approval_requested=NotificationRule(
            enabled=False,
            channels=(NotificationChannel.TOAST,),
        ),
    )
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=lambda: config,
    )
    emitted = service.emit(
        notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
        title="Approval Required",
        body="approval pending",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
    )

    assert emitted is False
    assert hub.events == []
