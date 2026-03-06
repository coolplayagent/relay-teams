# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast

import pytest

from agent_teams.agents.core.meta_agent import MetaAgent
from agent_teams.runs.enums import RunEventType
from agent_teams.runs.manager import RunManager
from agent_teams.runs.models import IntentInput
from agent_teams.notifications import (
    NotificationChannel,
    NotificationConfig,
    NotificationRule,
    NotificationService,
)
from agent_teams.agents.management.instance_pool import InstancePool
from agent_teams.runs.injection_queue import RunInjectionManager
from agent_teams.runs.control import RunControlManager
from agent_teams.runs.event_stream import RunEventHub
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.session_models import SessionRecord
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.task_repo import TaskRepository


class _MetaAgent:
    def __init__(self) -> None:
        pass

    async def handle_intent(self, intent, trace_id: str | None = None):
        await asyncio.sleep(0.01)
        raise AssertionError("not expected in this test")


class _AgentRepo:
    def list_running(self, run_id: str):
        return ()

    def get_coordinator_instance_id(self, session_id: str) -> str | None:
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


class _InstancePool:
    def mark_stopped(self, instance_id: str):
        return None

    def mark_failed(self, instance_id: str):
        return None


class _EventBus:
    def emit(self, event) -> None:
        return None


class _SessionRepo:
    def get(self, session_id: str) -> SessionRecord:
        return SessionRecord(session_id=session_id)

    def create(
        self, session_id: str, metadata: dict[str, str] | None = None
    ) -> SessionRecord:
        return SessionRecord(session_id=session_id, metadata=metadata or {})


def _make_run_manager(control: RunControlManager) -> RunManager:
    hub = RunEventHub()
    injection = RunInjectionManager()
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=cast(AgentInstanceRepository, cast(object, _AgentRepo())),
        task_repo=cast(TaskRepository, cast(object, _TaskRepo())),
        message_repo=cast(MessageRepository, cast(object, _MessageRepo())),
        instance_pool=cast(InstancePool, cast(object, _InstancePool())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
    )
    return RunManager(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
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
        manager.create_run(IntentInput(session_id="session-1", intent="hello"))


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
        instance_pool=cast(InstancePool, cast(object, _InstancePool())),
        event_bus=cast(EventLog, cast(object, _EventBus())),
    )
    manager = RunManager(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
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

    run_id, _ = manager.create_run(IntentInput(session_id="session-1", intent="hello"))
    queue = hub.subscribe(run_id)
    manager.stop_run(run_id)

    event = queue.get_nowait()
    assert event.event_type == RunEventType.RUN_STOPPED
    notification_event = queue.get_nowait()
    assert notification_event.event_type == RunEventType.NOTIFICATION_REQUESTED
