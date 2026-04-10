# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import cast

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.notifications import (
    NotificationConfig,
    NotificationChannel,
    NotificationContext,
    NotificationRule,
    NotificationService,
    NotificationType,
    default_notification_config,
)
from relay_teams.sessions.runs.event_stream import RunEventHub


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


class _FakeDispatcher:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def dispatch(self, request: object) -> None:
        self.requests.append(request)


class _FailingDispatcher:
    def dispatch(self, request: object) -> None:
        _ = request
        raise RuntimeError("dispatcher boom")


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


def test_emit_dispatches_to_custom_dispatchers() -> None:
    hub = _FakeRunEventHub()
    dispatcher = _FakeDispatcher()
    config = NotificationConfig(
        run_failed=NotificationRule(
            enabled=True,
            channels=(NotificationChannel.FEISHU,),
        ),
    )
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=lambda: config,
        dispatchers=(dispatcher,),
    )

    emitted = service.emit(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-1 failed.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
    )

    assert emitted is True
    assert len(dispatcher.requests) == 1


def test_emit_continues_after_dispatcher_failure() -> None:
    hub = _FakeRunEventHub()
    failing_dispatcher = _FailingDispatcher()
    succeeding_dispatcher = _FakeDispatcher()
    config = NotificationConfig(
        run_completed=NotificationRule(
            enabled=True,
            channels=(NotificationChannel.FEISHU,),
        ),
    )
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=lambda: config,
        dispatchers=(failing_dispatcher, succeeding_dispatcher),
    )

    emitted = service.emit(
        notification_type=NotificationType.RUN_COMPLETED,
        title="Run Completed",
        body="Run run-1 completed successfully.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
    )

    assert emitted is True
    assert len(hub.events) == 1
    assert len(succeeding_dispatcher.requests) == 1


def test_default_notification_config_enables_feishu_run_delivery() -> None:
    config = default_notification_config()

    assert config.run_completed.enabled is True
    assert config.run_completed.channels == (
        NotificationChannel.TOAST,
        NotificationChannel.FEISHU,
    )
    assert config.run_failed.channels == (
        NotificationChannel.BROWSER,
        NotificationChannel.TOAST,
        NotificationChannel.FEISHU,
    )
    assert config.monitor_triggered.enabled is True
    assert config.monitor_triggered.channels == (
        NotificationChannel.BROWSER,
        NotificationChannel.TOAST,
    )
