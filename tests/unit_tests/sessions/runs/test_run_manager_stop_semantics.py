# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import cast

import pytest

from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.media import content_parts_from_text
from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunResult
from agent_teams.notifications import (
    NotificationChannel,
    NotificationConfig,
    NotificationRule,
    NotificationService,
)
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.agents.tasks.task_repository import TaskRepository


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


def _make_run_manager(
    control: RunControlManager,
    *,
    background_task_manager: object | None = None,
) -> RunManager:
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
    return RunManager(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
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
    manager = _make_run_manager(control)

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
    manager = RunManager(
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
    manager = RunManager(
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


def test_worker_finalization_only_stops_foreground_exec_sessions() -> None:
    control = RunControlManager()
    background_task_manager = _CapturingBackgroundTaskManager()
    manager = _make_run_manager(
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
    manager = RunManager(
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
            output=content_parts_from_text("好"),
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
    assert notification_payload["body"] == "好"
