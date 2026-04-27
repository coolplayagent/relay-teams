from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.run_followups import RunFollowupRouter
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_models import IntentInput, RunEvent
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRecord
from relay_teams.sessions.runs.user_question_repository import UserQuestionRepository
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


class _LoopGuardRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.events.append(event)
            return
        raise AssertionError("sync publish must not run on the event loop")

    async def publish_async(self, event: RunEvent) -> None:
        self.events.append(event)


class _RecordingSessionRepo:
    def __init__(self) -> None:
        self.started_sessions: list[str] = []

    def mark_started(self, session_id: str) -> object:
        self.started_sessions.append(session_id)
        return object()


def _build_router(run_event_hub: RunEventHub) -> RunFollowupRouter:
    return RunFollowupRouter(
        injection_manager=cast(RunInjectionManager, object()),
        run_control_manager=cast(RunControlManager, object()),
        active_run_registry=cast(ActiveSessionRunRegistry, object()),
        session_repo=cast(SessionRepository, object()),
        run_event_hub=run_event_hub,
        get_background_task_manager=lambda: cast(BackgroundTaskManager | None, None),
        get_background_task_service=lambda: cast(BackgroundTaskService | None, None),
        get_run_intent_repo=lambda: cast(RunIntentRepository | None, None),
        get_approval_ticket_repo=lambda: cast(ApprovalTicketRepository | None, None),
        get_agent_repo=lambda: cast(AgentInstanceRepository | None, None),
        get_user_question_repo=lambda: cast(UserQuestionRepository | None, None),
        require_agent_repo=lambda: cast(AgentInstanceRepository, object()),
        require_message_repo=lambda: cast(MessageRepository, object()),
        require_task_repo=lambda: cast(TaskRepository, object()),
        runtime_for_run=lambda _: cast(RunRuntimeRecord | None, None),
        ensure_session=lambda session_id: session_id,
        create_run=_create_run,
        ensure_run_started=lambda _: None,
        remember_active_run=lambda _, __: None,
    )


def _create_run(_: IntentInput, __: InjectionSource) -> tuple[str, str]:
    return "run-created", "session-created"


class _RecordingRunFollowupRouter(RunFollowupRouter):
    def __init__(
        self,
        *,
        active_run_registry: ActiveSessionRunRegistry | None = None,
        session_repo: SessionRepository | None = None,
        create_run_async: Callable[
            [IntentInput, InjectionSource], Awaitable[tuple[str, str]]
        ]
        | None = None,
        ensure_run_started_async: Callable[[str], Awaitable[None]] | None = None,
        has_active_tasks: bool = False,
    ) -> None:
        self.appended_instances: list[tuple[str, str, str]] = []
        self.appended_coordinators: list[tuple[str, str]] = []
        self.started_runs: list[str] = []
        self.created_intents: list[IntentInput] = []
        self.remembered_runs: list[tuple[str, str]] = []
        self.instance_can_enqueue = True
        self.coordinator_can_enqueue: dict[str, bool] = {}
        self.append_instance_result = True
        self.append_coordinator_result: dict[str, bool] = {}
        self.has_active_tasks = has_active_tasks
        super().__init__(
            injection_manager=cast(RunInjectionManager, object()),
            run_control_manager=RunControlManager(),
            active_run_registry=active_run_registry or ActiveSessionRunRegistry(),
            session_repo=session_repo or cast(SessionRepository, object()),
            run_event_hub=cast(RunEventHub, _LoopGuardRunEventHub()),
            get_background_task_manager=lambda: cast(
                BackgroundTaskManager | None, None
            ),
            get_background_task_service=lambda: cast(
                BackgroundTaskService | None, None
            ),
            get_run_intent_repo=lambda: cast(RunIntentRepository | None, None),
            get_approval_ticket_repo=lambda: cast(
                ApprovalTicketRepository | None, None
            ),
            get_agent_repo=lambda: cast(AgentInstanceRepository | None, None),
            get_user_question_repo=lambda: cast(UserQuestionRepository | None, None),
            require_agent_repo=lambda: cast(AgentInstanceRepository, object()),
            require_message_repo=lambda: cast(MessageRepository, object()),
            require_task_repo=lambda: cast(TaskRepository, object()),
            runtime_for_run=lambda _: cast(RunRuntimeRecord | None, None),
            ensure_session=lambda session_id: session_id,
            create_run=self._record_create_run,
            ensure_run_started=self._record_run_started,
            remember_active_run=self._record_remember_active_run,
            create_run_async=create_run_async,
            ensure_run_started_async=ensure_run_started_async,
        )

    def find_task_for_instance(self, *, run_id: str, instance_id: str) -> str | None:
        _ = (run_id, instance_id)
        return "task-existing"

    def can_enqueue_followup_to_instance(
        self, *, run_id: str, instance_id: str
    ) -> bool:
        _ = (run_id, instance_id)
        return self.instance_can_enqueue

    def append_followup_to_instance(
        self,
        *,
        run_id: str,
        instance_id: str,
        task_id: str,
        content: str,
        enqueue: bool,
        source: InjectionSource,
    ) -> bool:
        _ = (enqueue, source)
        self.appended_instances.append((run_id, instance_id, task_id))
        _ = content
        return self.append_instance_result

    def can_enqueue_followup_to_coordinator(self, run_id: str) -> bool:
        return self.coordinator_can_enqueue.get(run_id, False)

    def append_followup_to_coordinator(
        self,
        run_id: str,
        content: str,
        *,
        enqueue: bool,
        source: InjectionSource = InjectionSource.USER,
    ) -> bool:
        _ = (enqueue, source)
        self.appended_coordinators.append((run_id, content))
        return self.append_coordinator_result.get(run_id, True)

    def has_active_background_tasks(self, run_id: str) -> bool:
        _ = run_id
        return self.has_active_tasks

    def _record_create_run(
        self,
        intent: IntentInput,
        source: InjectionSource,
    ) -> tuple[str, str]:
        _ = source
        self.created_intents.append(intent)
        return "run-created-sync", "session-1"

    def _record_run_started(self, run_id: str) -> None:
        self.started_runs.append(run_id)

    def _record_remember_active_run(self, session_id: str, run_id: str) -> None:
        self.remembered_runs.append((session_id, run_id))


def _background_record() -> BackgroundTaskRecord:
    return BackgroundTaskRecord(
        background_task_id="background-task-1",
        run_id="run-source",
        session_id="session-1",
        kind=BackgroundTaskKind.SUBAGENT,
        instance_id="instance-1",
        role_id="role-1",
        tool_call_id="tool-call-1",
        title="Explorer",
        command="subagent:Explorer",
        cwd="/workspace",
        execution_mode="background",
        status=BackgroundTaskStatus.COMPLETED,
        exit_code=0,
        recent_output=("done",),
        output_excerpt="done",
        log_path="",
    )


@pytest.mark.asyncio
async def test_publish_injection_event_uses_async_publish_on_running_loop() -> None:
    run_event_hub = _LoopGuardRunEventHub()
    router = _build_router(cast(RunEventHub, run_event_hub))
    record = AgentRuntimeRecord(
        run_id="run-1",
        trace_id="run-1",
        session_id="session-1",
        instance_id="instance-1",
        role_id="role-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
        status=InstanceStatus.RUNNING,
    )

    router.publish_injection_event(run_id="run-1", record=record, payload="{}")
    for _ in range(5):
        if run_event_hub.events:
            break
        await asyncio.sleep(0)

    assert [event.event_type for event in run_event_hub.events] == [
        RunEventType.INJECTION_ENQUEUED
    ]


@pytest.mark.asyncio
async def test_background_task_completion_async_enqueues_to_origin_instance() -> None:
    router = _RecordingRunFollowupRouter()

    await router.handle_background_task_completion_async(
        record=_background_record(),
        message="background finished",
    )

    assert router.appended_instances == [("run-source", "instance-1", "task-existing")]


@pytest.mark.asyncio
async def test_route_system_message_async_enqueues_to_source_coordinator() -> None:
    router = _RecordingRunFollowupRouter()
    router.instance_can_enqueue = False
    router.coordinator_can_enqueue = {"run-source": True}

    await router.route_system_message_async(
        source_run_id="run-source",
        session_id="session-1",
        preferred_instance_id="instance-1",
        role_id="role-1",
        task_id_fallback="task-fallback",
        message="coordinator follow-up",
        allow_coordinator=True,
        event_prefix="test.followup",
        payload={"test": "payload"},
    )

    assert router.appended_coordinators == [("run-source", "coordinator follow-up")]


@pytest.mark.asyncio
async def test_route_system_message_async_enqueues_to_other_active_run() -> None:
    active_run_registry = ActiveSessionRunRegistry()
    active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-active",
    )
    router = _RecordingRunFollowupRouter(active_run_registry=active_run_registry)
    router.instance_can_enqueue = False
    router.coordinator_can_enqueue = {"run-active": True}

    await router.route_system_message_async(
        source_run_id="run-source",
        session_id="session-1",
        preferred_instance_id="instance-1",
        role_id="role-1",
        task_id_fallback="task-fallback",
        message="active follow-up",
        allow_coordinator=True,
        event_prefix="test.followup",
        payload={"test": "payload"},
    )

    assert router.appended_coordinators == [("run-active", "active follow-up")]


@pytest.mark.asyncio
async def test_route_system_message_async_skips_unroutable_background_completion() -> (
    None
):
    router = _RecordingRunFollowupRouter()
    router.instance_can_enqueue = False

    await router.route_system_message_async(
        source_run_id="run-source",
        session_id="session-1",
        preferred_instance_id="instance-1",
        role_id="role-1",
        task_id_fallback="task-fallback",
        message="dropped follow-up",
        allow_coordinator=True,
        event_prefix="test.followup",
        payload={"test": "payload"},
        spawn_if_unroutable=False,
    )

    assert router.created_intents == []
    assert router.started_runs == []


@pytest.mark.asyncio
async def test_spawn_system_followup_run_async_uses_async_entrypoints() -> None:
    session_repo = _RecordingSessionRepo()
    created_sources: list[InjectionSource] = []
    started_runs: list[str] = []

    async def _create_run_async(
        intent: IntentInput,
        source: InjectionSource,
    ) -> tuple[str, str]:
        created_sources.append(source)
        assert intent.session_id == "session-1"
        return "run-created-async", "session-1"

    async def _ensure_started_async(run_id: str) -> None:
        started_runs.append(run_id)

    router = _RecordingRunFollowupRouter(
        session_repo=cast(SessionRepository, session_repo),
        create_run_async=_create_run_async,
        ensure_run_started_async=_ensure_started_async,
        has_active_tasks=True,
    )

    run_id = await router.spawn_system_followup_run_async(
        source_run_id="run-source",
        session_id="session-1",
        message="spawn follow-up",
        event_prefix="test.followup",
        payload={"test": "payload"},
    )

    assert run_id == "run-created-async"
    assert created_sources == [InjectionSource.SYSTEM]
    assert started_runs == ["run-created-async"]
    assert session_repo.started_sessions == ["session-1"]
    assert router.remembered_runs == [("session-1", "run-source")]


@pytest.mark.asyncio
async def test_spawn_system_followup_run_async_falls_back_to_sync_entrypoints() -> None:
    session_repo = _RecordingSessionRepo()
    router = _RecordingRunFollowupRouter(
        session_repo=cast(SessionRepository, session_repo)
    )

    run_id = await router.spawn_system_followup_run_async(
        source_run_id="run-source",
        session_id="session-1",
        message="spawn follow-up",
        event_prefix="test.followup",
        payload={"test": "payload"},
    )

    assert run_id == "run-created-sync"
    assert router.started_runs == ["run-created-sync"]
    assert session_repo.started_sessions == ["session-1"]
    assert [intent.session_id for intent in router.created_intents] == ["session-1"]
