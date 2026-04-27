# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import time
from typing import cast

import pytest

from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName
from relay_teams.notifications import (
    NotificationConfig,
    NotificationChannel,
    NotificationContext,
    NotificationRequest,
    NotificationRule,
    NotificationService,
    NotificationType,
    default_notification_config,
)
from relay_teams.notifications.notification_service import (
    _notification_hook_failure_payload,
)
from relay_teams.sessions.runs.event_stream import RunEventHub


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)

    def loop_for_run(self, run_id: str) -> asyncio.AbstractEventLoop | None:
        _ = run_id
        return None


class _FakeDispatcher:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def dispatch(self, request: object) -> None:
        self.requests.append(request)


class _FailingDispatcher:
    def dispatch(self, request: object) -> None:
        _ = request
        raise RuntimeError("dispatcher boom")


class _FakeHookService:
    def __init__(self) -> None:
        self.event_inputs: list[object] = []
        self.published_count_during_execute: int | None = None

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        self.published_count_during_execute = len(
            cast(_FakeRunEventHub, run_event_hub).events
        )
        self.event_inputs.append(event_input)
        return HookDecisionBundle(decision=HookDecisionType.OBSERVE)


class _ContextHookService(_FakeHookService):
    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = await super().execute(event_input=event_input, run_event_hub=run_event_hub)
        return HookDecisionBundle(
            decision=HookDecisionType.OBSERVE,
            additional_context=("review notification context",),
        )


class _AsyncSchedulingHookService(_FakeHookService):
    def __init__(self) -> None:
        super().__init__()
        self.completed = False
        self.release: asyncio.Event | None = None

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = await super().execute(event_input=event_input, run_event_hub=run_event_hub)
        release = asyncio.Event()
        self.release = release

        async def complete_later() -> None:
            await release.wait()
            self.completed = True

        _ = asyncio.create_task(complete_later())
        return HookDecisionBundle(decision=HookDecisionType.OBSERVE)


class _FailingHookService:
    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = (event_input, run_event_hub)
        raise RuntimeError("hook boom")


class _FakeInjectionRecord:
    def __init__(self, *, source: InjectionSource, content: object) -> None:
        self.source = source
        self.content = content


class _FakeInjectionManager:
    def __init__(self) -> None:
        self.records: list[_FakeInjectionRecord] = []

    def is_active(self, run_id: str) -> bool:
        return run_id == "run-1"

    def enqueue(
        self,
        run_id: str,
        recipient_instance_id: str,
        *,
        source: InjectionSource,
        content: object,
    ) -> object:
        _ = (run_id, recipient_instance_id)
        record = _FakeInjectionRecord(source=source, content=content)
        self.records.append(record)
        return record


class _RecursiveHookService:
    def __init__(self, service: NotificationService) -> None:
        self.service = service
        self.calls = 0

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = (event_input, run_event_hub)
        self.calls += 1
        _ = self.service.emit(
            notification_type=NotificationType.RUN_FAILED,
            title="Nested",
            body="Nested notification.",
            context=NotificationContext(
                session_id="session-1",
                run_id="run-1",
                trace_id="trace-1",
            ),
        )
        return HookDecisionBundle(decision=HookDecisionType.OBSERVE)


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
            session_mode="orchestration",
            run_kind="generate_image",
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


def test_notification_context_defaults_to_matchable_scope_metadata() -> None:
    context = NotificationContext(
        session_id="session-1",
        run_id="run-1",
        trace_id="trace-1",
    )

    assert context.session_mode == "normal"
    assert context.run_kind == "conversation"


def test_emit_runs_notification_hook_before_publish() -> None:
    hub = _FakeRunEventHub()
    hook_service = _FakeHookService()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=hook_service,
    )

    emitted = service.emit(
        notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
        title="Approval Required",
        body="spec_coder requests approval for write.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            session_mode="orchestration",
            run_kind="generate_image",
            tool_call_id="toolcall-1",
            tool_name="write",
        ),
    )

    assert emitted is True
    assert len(hook_service.event_inputs) == 1
    event_input = hook_service.event_inputs[0]
    assert getattr(event_input, "event_name") == HookEventName.NOTIFICATION
    assert getattr(event_input, "notification_type") == "tool_approval_requested"
    assert getattr(event_input, "tool_name") == "write"
    assert getattr(event_input, "session_mode") == "orchestration"
    assert getattr(event_input, "run_kind") == "generate_image"
    assert hook_service.published_count_during_execute == 0
    assert len(hub.events) == 1
    assert hub.events[0].event_type == RunEventType.NOTIFICATION_REQUESTED


def test_emit_enqueues_notification_hook_additional_context() -> None:
    hub = _FakeRunEventHub()
    injection_manager = _FakeInjectionManager()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=_ContextHookService(),
        injection_manager=injection_manager,
    )

    emitted = service.emit(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-1 failed.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            instance_id="instance-1",
        ),
    )

    assert emitted is True
    assert len(injection_manager.records) == 1
    assert injection_manager.records[0].source == InjectionSource.SYSTEM
    assert injection_manager.records[0].content == "review notification context"


def test_emit_leaves_sync_notification_async_hook_tasks_on_background_loop() -> None:
    hub = _FakeRunEventHub()
    hook_service = _AsyncSchedulingHookService()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=hook_service,
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
    assert hook_service.completed is False
    assert hook_service.release is not None
    service._get_hook_loop().loop.call_soon_threadsafe(hook_service.release.set)
    for _ in range(20):
        if hook_service.completed:
            break
        time.sleep(0.01)
    assert hook_service.completed is True


def test_emit_sync_notification_without_hook_service_completes() -> None:
    hub = _FakeRunEventHub()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
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
    assert len(hub.events) == 1


@pytest.mark.asyncio
async def test_emit_schedules_notification_hook_on_active_event_loop() -> None:
    hub = _FakeRunEventHub()
    hook_service = _FakeHookService()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=hook_service,
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

    for _ in range(20):
        if hook_service.event_inputs:
            break
        await asyncio.sleep(0)

    assert emitted is True
    assert len(hook_service.event_inputs) == 1
    assert len(hub.events) == 1


@pytest.mark.asyncio
async def test_emit_skips_notification_hook_recursion() -> None:
    hub = _FakeRunEventHub()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
    )
    recursive_hook_service = _RecursiveHookService(service)
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=recursive_hook_service,
    )
    recursive_hook_service.service = service

    emitted = await service.emit_async(
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
    assert recursive_hook_service.calls == 1
    notification_events = [
        event
        for event in hub.events
        if event.event_type == RunEventType.NOTIFICATION_REQUESTED
    ]
    assert len(notification_events) == 2


def test_emit_continues_after_notification_hook_failure() -> None:
    hub = _FakeRunEventHub()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=_FailingHookService(),
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
    assert len(hub.events) == 1


def test_notification_hook_additional_context_requires_active_instance() -> None:
    hub = _FakeRunEventHub()
    injection_manager = _FakeInjectionManager()
    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=_ContextHookService(),
        injection_manager=injection_manager,
    )

    no_instance_emitted = service.emit(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-1 failed.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
    )
    inactive_emitted = service.emit(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-2 failed.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-2",
            trace_id="trace-2",
            instance_id="instance-2",
        ),
    )

    assert no_instance_emitted is True
    assert inactive_emitted is True
    assert injection_manager.records == []


def test_notification_hook_ignores_blank_additional_context() -> None:
    hub = _FakeRunEventHub()
    injection_manager = _FakeInjectionManager()

    class _BlankContextHookService(_FakeHookService):
        async def execute(
            self, *, event_input: object, run_event_hub: object
        ) -> HookDecisionBundle:
            _ = await super().execute(
                event_input=event_input,
                run_event_hub=run_event_hub,
            )
            return HookDecisionBundle(
                decision=HookDecisionType.OBSERVE,
                additional_context=(" ", "\n"),
            )

    service = NotificationService(
        run_event_hub=cast(RunEventHub, cast(object, hub)),
        get_config=default_notification_config,
        hook_service=_BlankContextHookService(),
        injection_manager=injection_manager,
    )

    emitted = service.emit(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-1 failed.",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
            instance_id="instance-1",
        ),
    )

    assert emitted is True
    assert injection_manager.records == []


def test_notification_hook_failure_payload() -> None:
    request = NotificationRequest(
        notification_type=NotificationType.RUN_FAILED,
        title="Run Failed",
        body="Run run-1 failed.",
        channels=(NotificationChannel.BROWSER,),
        dedupe_key="run_failed:run-1",
        context=NotificationContext(
            session_id="session-1",
            run_id="run-1",
            trace_id="trace-1",
        ),
    )

    assert _notification_hook_failure_payload(request) == {
        "notification_type": "run_failed",
        "run_id": "run-1",
        "session_id": "session-1",
        "dedupe_key": "run_failed:run-1",
    }


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


@pytest.mark.asyncio
async def test_emit_async_returns_false_when_type_is_disabled() -> None:
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

    emitted = await service.emit_async(
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


@pytest.mark.asyncio
async def test_emit_async_continues_after_dispatcher_failure() -> None:
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

    emitted = await service.emit_async(
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
