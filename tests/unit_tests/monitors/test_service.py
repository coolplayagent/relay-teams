# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from relay_teams.monitors import (
    MonitorAction,
    MonitorEventEnvelope,
    MonitorRepository,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub


class _FakeMonitorSink:
    def __init__(self) -> None:
        self.calls: list[
            tuple[MonitorSubscriptionRecord, MonitorEventEnvelope, str]
        ] = []

    def handle_monitor_trigger(
        self,
        *,
        subscription: MonitorSubscriptionRecord,
        envelope: MonitorEventEnvelope,
        message: str,
    ) -> None:
        self.calls.append((subscription, envelope, message))


def test_monitor_service_dedupes_and_auto_stops(tmp_path) -> None:
    hub = RunEventHub()
    repository = MonitorRepository(tmp_path / "monitor-service.db")
    service = MonitorService(repository=repository, run_event_hub=hub)
    sink = _FakeMonitorSink()
    service.bind_action_sink(sink)
    queue = hub.subscribe("run-1")

    created = service.create_monitor(
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background_task_1",
        rule=MonitorRule(
            event_names=("background_task.line",),
            text_patterns_any=("ERROR",),
            auto_stop_on_first_match=True,
        ),
        action=MonitorAction(),
        created_by_instance_id="inst-1",
        created_by_role_id="role-1",
        tool_call_id="toolcall-1",
    )

    envelope = MonitorEventEnvelope(
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background_task_1",
        event_name="background_task.line",
        body_text="ERROR database down",
        dedupe_key="delivery-1",
    )
    first = service.emit(envelope)
    second = service.emit(envelope)

    assert len(first) == 1
    assert second == ()
    assert len(sink.calls) == 1
    assert sink.calls[0][1].body_text == "ERROR database down"

    stored = service.list_for_run("run-1")[0]
    assert stored.monitor_id == created.monitor_id
    assert stored.trigger_count == 1
    assert stored.status.value == "stopped"

    event_types: list[RunEventType] = []
    while not queue.empty():
        event_types.append(queue.get_nowait().event_type)
    assert event_types == [
        RunEventType.MONITOR_CREATED,
        RunEventType.MONITOR_TRIGGERED,
        RunEventType.MONITOR_STOPPED,
    ]


@pytest.mark.asyncio
async def test_monitor_service_async_dedupes_and_stops(tmp_path) -> None:
    hub = RunEventHub()
    repository = MonitorRepository(tmp_path / "monitor-service-async.db")
    service = MonitorService(repository=repository, run_event_hub=hub)
    sink = _FakeMonitorSink()
    service.bind_action_sink(sink)
    queue = hub.subscribe("run-1")

    created = await service.create_monitor_async(
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background_task_1",
        rule=MonitorRule(
            event_names=("background_task.line",),
            text_patterns_any=("ERROR",),
            max_triggers=2,
        ),
        action=MonitorAction(),
        created_by_instance_id="inst-1",
        created_by_role_id="role-1",
        tool_call_id="toolcall-1",
    )

    envelope = MonitorEventEnvelope(
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background_task_1",
        event_name="background_task.line",
        body_text="ERROR database down",
        dedupe_key="delivery-1",
    )
    first = await service.emit_async(envelope)
    second = await service.emit_async(envelope)
    stopped = await service.stop_for_run_async(
        run_id="run-1",
        monitor_id=created.monitor_id,
    )

    assert len(first) == 1
    assert second == ()
    assert stopped.status.value == "stopped"
    assert len(sink.calls) == 1

    stored = (await service.list_for_run_async("run-1"))[0]
    assert stored.monitor_id == created.monitor_id
    assert stored.trigger_count == 1
    assert stored.status.value == "stopped"

    event_types: list[RunEventType] = []
    while not queue.empty():
        event_types.append(queue.get_nowait().event_type)
    assert event_types == [
        RunEventType.MONITOR_CREATED,
        RunEventType.MONITOR_TRIGGERED,
        RunEventType.MONITOR_STOPPED,
    ]


def test_monitor_service_respects_cooldown(tmp_path) -> None:
    hub = RunEventHub()
    repository = MonitorRepository(tmp_path / "monitor-service-cooldown.db")
    service = MonitorService(repository=repository, run_event_hub=hub)
    sink = _FakeMonitorSink()
    service.bind_action_sink(sink)

    service.create_monitor(
        run_id="run-1",
        session_id="session-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key="background_task_1",
        rule=MonitorRule(
            event_names=("background_task.line",),
            text_patterns_any=("ERROR",),
            cooldown_seconds=10,
        ),
        action=MonitorAction(),
        created_by_instance_id="inst-1",
        created_by_role_id="role-1",
        tool_call_id="toolcall-1",
    )

    now = datetime.now(tz=UTC)
    first = service.emit(
        MonitorEventEnvelope(
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="background_task_1",
            event_name="background_task.line",
            body_text="ERROR one",
            dedupe_key="event-1",
            occurred_at=now,
        )
    )
    second = service.emit(
        MonitorEventEnvelope(
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="background_task_1",
            event_name="background_task.line",
            body_text="ERROR two",
            dedupe_key="event-2",
            occurred_at=now + timedelta(seconds=5),
        )
    )
    third = service.emit(
        MonitorEventEnvelope(
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="background_task_1",
            event_name="background_task.line",
            body_text="ERROR three",
            dedupe_key="event-3",
            occurred_at=now + timedelta(seconds=11),
        )
    )

    assert len(first) == 1
    assert second == ()
    assert len(third) == 1
    assert [call[1].body_text for call in sink.calls] == ["ERROR one", "ERROR three"]
