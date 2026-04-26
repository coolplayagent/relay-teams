# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import cast

from relay_teams.sessions.runs.enums import InjectionSource

import pytest

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_models import IntentInput, RunResult
from relay_teams.notifications import (
    NotificationChannel,
    NotificationConfig,
    NotificationRule,
    NotificationService,
    NotificationType,
)
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookService,
)


class _MetaAgent:
    def __init__(self) -> None:
        pass

    async def handle_intent(self, intent, trace_id: str | None = None):
        await asyncio.sleep(0.01)
        raise AssertionError("not expected in this test")


class _AgentRepo:
    def list_running(self, run_id: str):
        return ()

    def get_coordinator_instance_id(
        self, *, run_id: str, session_id: str
    ) -> str | None:
        _ = run_id
        _ = session_id
        return None

    def get_instance(self, instance_id: str):
        raise KeyError(instance_id)

    def mark_status(self, instance_id: str, status) -> None:
        return None


class _TaskRepo:
    def list_by_trace(self, trace_id: str):
        return ()

    def update_status(self, **kwargs) -> None:
        return None


class _MessageRepo:
    def append(self, **kwargs) -> None:
        return None


class _EventBus:
    def emit(self, event) -> None:
        return None


class _CapturingBackgroundTaskManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def stop_all_for_run(
        self,
        *,
        run_id: str,
        reason: str,
        execution_mode: str | None = None,
    ) -> None:
        self.calls.append((run_id, reason, execution_mode))


class _RunRuntimeRepo:
    def list_by_session(self, session_id: str):
        _ = session_id
        return ()


class _FailingRunEventHub:
    def publish(self, event) -> None:
        _ = event
        raise sqlite3.OperationalError("database is locked")


class _FailingRunRuntimeRepo:
    def update(self, run_id: str, **changes) -> None:
        _ = (run_id, changes)
        raise sqlite3.OperationalError("database is locked")

    def get(self, run_id: str):
        _ = run_id
        raise sqlite3.OperationalError("database is locked")

    def list_by_session(self, session_id: str):
        _ = session_id
        return ()


class _SessionRepo:
    def get(self, session_id: str) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            workspace_id="default",
        )

    def create(
        self, session_id: str, metadata: dict[str, str] | None = None
    ) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            workspace_id="default",
            metadata=metadata or {},
        )

    def mark_started(self, session_id: str) -> SessionRecord:
        return self.get(session_id)


def _make_run_service(
    control: RunControlManager,
    *,
    background_task_manager: object | None = None,
    meta_agent: object | None = None,
) -> SessionRunService:
    hub = RunEventHub()
    injection = RunInjectionManager()
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=cast(AgentInstanceRepository, cast(object, _AgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _TaskRepo())),
        message_repo=cast(MessageRepository, cast(object, _MessageRepo())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
        run_runtime_repo=cast(RunRuntimeRepository, cast(object, _RunRuntimeRepo())),
    )
    return SessionRunService(
        meta_agent=cast(MetaAgent, cast(object, meta_agent or _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=ActiveSessionRunRegistry(),
        background_task_manager=(
            cast(BackgroundTaskManager, cast(object, background_task_manager))
            if background_task_manager is not None
            else None
        ),
    )


def test_create_run_blocked_when_paused_subagent_exists() -> None:
    control = RunControlManager()
    control.pause_subagent(
        session_id="session-1",
        run_id="run-1",
        instance_id="inst-1",
        role_id="generalist",
        task_id="task-1",
    )
    manager = _make_run_service(control)

    with pytest.raises(RuntimeError):
        manager.create_run(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
            )
        )


def test_stop_pending_run_emits_run_stopped_event() -> None:
    control = RunControlManager()
    hub = RunEventHub()
    injection = RunInjectionManager()
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=cast(AgentInstanceRepository, cast(object, _AgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _TaskRepo())),
        message_repo=cast(MessageRepository, cast(object, _MessageRepo())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
        run_runtime_repo=cast(RunRuntimeRepository, cast(object, _RunRuntimeRepo())),
    )
    manager = SessionRunService(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=ActiveSessionRunRegistry(),
        notification_service=NotificationService(
            run_event_hub=hub,
            get_config=lambda: NotificationConfig(
                run_stopped=NotificationRule(
                    enabled=True,
                    channels=(NotificationChannel.TOAST,),
                ),
            ),
        ),
    )

    run_id, _ = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("hello"),
        )
    )
    queue = hub.subscribe(run_id)
    manager.stop_run(run_id)

    event = queue.get_nowait()
    assert event.event_type == RunEventType.RUN_STOPPED
    notification_event = queue.get_nowait()
    assert notification_event.event_type == RunEventType.NOTIFICATION_REQUESTED


def test_worker_swallows_cleanup_failures_after_runner_exception() -> None:
    control = RunControlManager()
    injection = RunInjectionManager()
    manager = SessionRunService(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=cast(RunEventHub, cast(object, _FailingRunEventHub())),
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=ActiveSessionRunRegistry(),
        run_runtime_repo=cast(
            RunRuntimeRepository, cast(object, _FailingRunRuntimeRepo())
        ),
    )
    manager._running_run_ids.add("run-1")

    async def runner():
        raise RuntimeError("boom")

    asyncio.run(
        manager._worker(
            run_id="run-1",
            session_id="session-1",
            runner=runner,
        )
    )

    assert "run-1" not in manager._running_run_ids


def test_worker_finalization_only_stops_foreground_background_tasks() -> None:
    control = RunControlManager()
    background_task_manager = _CapturingBackgroundTaskManager()
    manager = _make_run_service(
        control,
        background_task_manager=background_task_manager,
    )
    manager._running_run_ids.add("run-1")

    async def runner() -> RunResult:
        return RunResult(
            trace_id="run-1",
            root_task_id="task-1",
            status="completed",
            output=content_parts_from_text("done"),
        )

    asyncio.run(
        manager._worker(
            run_id="run-1",
            session_id="session-1",
            runner=runner,
        )
    )

    assert background_task_manager.calls == [("run-1", "run_finalized", "foreground")]


def test_stop_active_runs_for_shutdown_requests_running_run_stop() -> None:
    control = RunControlManager()
    manager = _make_run_service(control)

    async def never_complete() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        task = asyncio.create_task(never_complete())
        control.register_run_task(
            run_id="run-1",
            session_id="session-1",
            task=task,
        )
        manager._running_run_ids.add("run-1")

        stopped = await manager.stop_active_runs_for_shutdown_async()

        assert stopped == 1
        assert control.is_run_stop_requested("run-1") is True
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled() is True

    asyncio.run(scenario())


def test_stop_active_runs_for_shutdown_skips_missing_and_failed_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = RunControlManager()
    manager = _make_run_service(control)
    stopped_run_ids: list[str] = []
    manager._running_run_ids.update({"ok-run", "missing-run"})
    manager._pending_runs["ok-run"] = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("hello"),
    )
    manager._pending_runs["broken-run"] = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("hello"),
    )

    async def fake_stop_run_local_async(run_id: str) -> None:
        stopped_run_ids.append(run_id)
        if run_id == "missing-run":
            raise KeyError(run_id)
        if run_id == "broken-run":
            raise RuntimeError("boom")

    monkeypatch.setattr(
        manager,
        "_stop_run_local_async",
        fake_stop_run_local_async,
    )

    stopped_count = asyncio.run(manager.stop_active_runs_for_shutdown_async())

    assert stopped_count == 1
    assert set(stopped_run_ids) == {"ok-run", "missing-run", "broken-run"}


def test_completed_notification_uses_final_run_output() -> None:
    control = RunControlManager()
    hub = RunEventHub()
    injection = RunInjectionManager()
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=cast(AgentInstanceRepository, cast(object, _AgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _TaskRepo())),
        message_repo=cast(MessageRepository, cast(object, _MessageRepo())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
        run_runtime_repo=cast(RunRuntimeRepository, cast(object, _RunRuntimeRepo())),
    )
    manager = SessionRunService(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=ActiveSessionRunRegistry(),
        notification_service=NotificationService(
            run_event_hub=hub,
            get_config=lambda: NotificationConfig(
                run_completed=NotificationRule(
                    enabled=True,
                    channels=(NotificationChannel.TOAST,),
                ),
            ),
        ),
    )

    run_id = "run-1"
    queue = hub.subscribe(run_id)

    async def runner() -> RunResult:
        return RunResult(
            trace_id=run_id,
            root_task_id="task-1",
            status="completed",
            output=content_parts_from_text("done"),
        )

    asyncio.run(
        manager._worker(
            run_id=run_id,
            session_id="session-1",
            runner=runner,
        )
    )

    notification_payload: dict[str, object] | None = None
    while not queue.empty():
        event = queue.get_nowait()
        if event.event_type == RunEventType.NOTIFICATION_REQUESTED:
            notification_payload = json.loads(event.payload_json)
            break

    assert notification_payload is not None
    assert notification_payload["body"] == "done"


def test_assistant_error_notification_uses_failed_channel() -> None:
    control = RunControlManager()
    hub = RunEventHub()
    injection = RunInjectionManager()
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=cast(AgentInstanceRepository, cast(object, _AgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _TaskRepo())),
        message_repo=cast(MessageRepository, cast(object, _MessageRepo())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
        run_runtime_repo=cast(RunRuntimeRepository, cast(object, _RunRuntimeRepo())),
    )
    manager = SessionRunService(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=ActiveSessionRunRegistry(),
        notification_service=NotificationService(
            run_event_hub=hub,
            get_config=lambda: NotificationConfig(
                run_failed=NotificationRule(
                    enabled=True,
                    channels=(NotificationChannel.TOAST,),
                ),
            ),
        ),
    )

    run_id = "run-1"
    queue = hub.subscribe(run_id)

    async def runner() -> RunResult:
        return RunResult(
            trace_id=run_id,
            root_task_id="task-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_message="provider rejected request",
            output=content_parts_from_text("provider rejected request"),
        )

    asyncio.run(
        manager._worker(
            run_id=run_id,
            session_id="session-1",
            runner=runner,
        )
    )

    notification_payload: dict[str, object] | None = None
    while not queue.empty():
        event = queue.get_nowait()
        if event.event_type == RunEventType.NOTIFICATION_REQUESTED:
            notification_payload = json.loads(event.payload_json)
            break

    assert notification_payload is not None
    assert (
        notification_payload["notification_type"] == NotificationType.RUN_FAILED.value
    )
    assert notification_payload["title"] == "Run Failed"
    assert notification_payload["body"] == "provider rejected request"


class _FakeRunHookService:
    def __init__(
        self,
        *,
        stop_decision: HookDecisionType = HookDecisionType.ALLOW,
        stop_bundles: tuple[HookDecisionBundle, ...] = (),
    ) -> None:
        self.stop_decision = stop_decision
        self.stop_bundles = list(stop_bundles)
        self.cleared_run_ids: list[str] = []
        self.events: list[HookEventName] = []
        self.snapshotted_run_ids: list[str] = []
        self.stop_failure_payloads: list[tuple[str, str]] = []

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = run_event_hub
        event_name = cast(HookEventName, getattr(event_input, "event_name"))
        self.events.append(event_name)
        if event_name == HookEventName.STOP:
            if self.stop_bundles:
                return self.stop_bundles.pop(0)
            return HookDecisionBundle(decision=self.stop_decision)
        if event_name == HookEventName.STOP_FAILURE:
            self.stop_failure_payloads.append(
                (
                    str(getattr(event_input, "error_code", "")),
                    str(getattr(event_input, "error_message", "")),
                )
            )
        return HookDecisionBundle(decision=HookDecisionType.ALLOW)

    def snapshot_run(self, run_id: str) -> None:
        self.snapshotted_run_ids.append(run_id)

    def clear_run(self, run_id: str) -> None:
        self.cleared_run_ids.append(run_id)


def test_stop_pending_run_does_not_invoke_completion_stop_hooks() -> None:
    control = RunControlManager()
    hook_service = _FakeRunHookService(stop_decision=HookDecisionType.RETRY)
    manager = _make_run_service(control)
    manager._hook_service = cast(HookService, hook_service)

    run_id, _ = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("hello"),
        )
    )
    manager.stop_run(run_id)

    assert run_id not in manager._pending_runs
    assert HookEventName.STOP not in hook_service.events


def test_finalize_run_clears_hook_runtime_state() -> None:
    control = RunControlManager()
    hook_service = _FakeRunHookService()
    manager = _make_run_service(control)
    manager._hook_service = cast(HookService, hook_service)
    manager._running_run_ids.add("run-1")

    manager._finalize_run(run_id="run-1", session_id="session-1")

    assert hook_service.cleared_run_ids == ["run-1"]


class _DirectRunMetaAgent:
    async def handle_intent(self, intent, trace_id: str | None = None):
        _ = intent
        return RunResult(
            trace_id=trace_id or "run-direct",
            root_task_id="task-direct-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
            output=content_parts_from_text("done"),
        )


class _RetryingDirectRunMetaAgent:
    def __init__(self) -> None:
        self.resume_calls = 0

    async def handle_intent(self, intent, trace_id: str | None = None):
        _ = intent
        return RunResult(
            trace_id=trace_id or "run-direct",
            root_task_id="task-direct-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
            output=content_parts_from_text("draft"),
        )

    async def resume_run(self, trace_id: str):
        self.resume_calls += 1
        return RunResult(
            trace_id=trace_id,
            root_task_id="task-direct-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
            output=content_parts_from_text("verified"),
        )


class _AssistantErrorMetaAgent:
    async def handle_intent(self, intent, trace_id: str | None = None):
        _ = intent
        return RunResult(
            trace_id=trace_id or "run-direct",
            root_task_id="task-direct-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_ERROR,
            error_code="provider_error",
            error_message="provider rejected request",
            output=content_parts_from_text("provider rejected request"),
        )


def test_direct_run_executes_session_hooks_and_clears_runtime_state() -> None:
    control = RunControlManager()
    hook_service = _FakeRunHookService()
    manager = _make_run_service(control, meta_agent=_DirectRunMetaAgent())
    manager._hook_service = cast(HookService, hook_service)

    result = asyncio.run(
        manager.run_intent(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
            )
        )
    )

    assert result.status == "completed"
    assert hook_service.events[:3] == [
        HookEventName.SESSION_START,
        HookEventName.STOP,
        HookEventName.SESSION_END,
    ]
    assert hook_service.cleared_run_ids == [result.trace_id]
    assert hook_service.snapshotted_run_ids == [result.trace_id]


def test_direct_run_retries_completion_when_stop_hook_requests_retry() -> None:
    control = RunControlManager()
    meta_agent = _RetryingDirectRunMetaAgent()
    hook_service = _FakeRunHookService(
        stop_bundles=(
            HookDecisionBundle(
                decision=HookDecisionType.RETRY,
                additional_context=("Need one more verification pass.",),
            ),
            HookDecisionBundle(decision=HookDecisionType.ALLOW),
        )
    )
    manager = _make_run_service(control, meta_agent=meta_agent)
    manager._hook_service = cast(HookService, hook_service)
    captured_followups: list[tuple[str, bool, InjectionSource]] = []
    manager._append_followup_to_coordinator = (
        lambda run_id, content, *, enqueue, source=InjectionSource.USER: (
            captured_followups.append((content, enqueue, source)) or True
        )
    )
    resume_calls: list[str] = []

    async def _resume_existing_run(run_id: str) -> RunResult:
        resume_calls.append(run_id)
        return RunResult(
            trace_id=run_id,
            root_task_id="task-direct-1",
            status="completed",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
            output=content_parts_from_text("verified"),
        )

    manager._resume_existing_run = _resume_existing_run

    result = asyncio.run(
        manager.run_intent(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
            )
        )
    )

    assert result.output_text == "verified"
    assert len(resume_calls) == 1
    assert captured_followups == [
        ("Need one more verification pass.", True, InjectionSource.SYSTEM)
    ]
    assert hook_service.events.count(HookEventName.STOP) == 2


def test_direct_run_publishes_stop_failure_for_assistant_error() -> None:
    control = RunControlManager()
    hook_service = _FakeRunHookService()
    manager = _make_run_service(control, meta_agent=_AssistantErrorMetaAgent())
    manager._hook_service = cast(HookService, hook_service)

    result = asyncio.run(
        manager.run_intent(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("hello"),
            )
        )
    )

    assert result.status == "failed"
    assert HookEventName.STOP not in hook_service.events
    assert HookEventName.STOP_FAILURE in hook_service.events
    assert hook_service.stop_failure_payloads == [
        ("provider_error", "provider rejected request")
    ]
