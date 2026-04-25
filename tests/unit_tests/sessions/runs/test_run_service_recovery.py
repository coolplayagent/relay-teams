# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable, Literal, cast

import pytest
from pydantic import JsonValue

from relay_teams.agents.orchestration.meta_agent import MetaAgent
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.media import (
    MediaAssetService,
    TextContentPart,
    content_parts_from_text,
)
from relay_teams.monitors import (
    MonitorAction,
    MonitorActionType,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
)
from relay_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from relay_teams.sessions.runs.run_control_manager import RunControlManager
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.injection_queue import RunInjectionManager
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.service import (
    BackgroundTaskService,
)
from relay_teams.sessions.runs.background_tasks.manager import (
    BackgroundTaskManager,
)
from relay_teams.sessions.runs.run_recovery import AutoRecoveryReason
from relay_teams.sessions.runs.run_service import SessionRunService
from relay_teams.sessions.runs.run_models import (
    AudioGenerationConfig,
    ImageGenerationConfig,
    IntentInput,
    RunEvent,
    RunKind,
    RunResult,
    VideoGenerationConfig,
)
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRecord,
    ApprovalTicketRepository,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.runs.todo_repository import TodoRepository
from relay_teams.sessions.runs.todo_service import TodoService
from relay_teams.sessions.runs.user_question_manager import (
    UserQuestionClosedError,
    UserQuestionManager,
)
from relay_teams.sessions.runs.user_question_models import (
    UserQuestionAnswer,
    UserQuestionAnswerSubmission,
    UserQuestionOption,
    UserQuestionPrompt,
    UserQuestionRequestStatus,
    UserQuestionSelection,
)
from relay_teams.sessions.runs.user_question_repository import (
    UserQuestionRepository,
    UserQuestionStatusConflictError,
)
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.session_models import SessionRecord
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.tools.runtime.approval_state import ToolApprovalManager
from relay_teams.tools.workspace_tools.shell_approval_repo import (
    ShellApprovalRepository,
    ShellApprovalScope,
)
from relay_teams.tools.workspace_tools.shell_policy import ShellRuntimeFamily
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


class _MetaAgent:
    async def handle_intent(
        self, intent, trace_id: str | None = None
    ):  # pragma: no cover
        raise AssertionError("not expected")

    async def resume_run(self, *, trace_id: str):  # pragma: no cover
        raise AssertionError(f"not expected: {trace_id}")


class _SessionRepo:
    def get(self, session_id: str) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            workspace_id="default",
        )

    def create(
        self,
        session_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            workspace_id="default",
            metadata=metadata or {},
        )

    def mark_started(self, session_id: str) -> SessionRecord:
        return self.get(session_id)


class _EventBus:
    def emit(self, event) -> None:
        _ = event


class _NativeImageProvider(LLMProvider):
    def __init__(self, output: tuple[TextContentPart, ...] | None = None) -> None:
        self.output = (
            output if output is not None else (TextContentPart(text="image ready"),)
        )
        self.requests: list[LLMRequest] = []

    async def generate_image(self, request: LLMRequest) -> tuple[TextContentPart, ...]:
        self.requests.append(request)
        return self.output

    async def generate_audio(self, request: LLMRequest) -> tuple[TextContentPart, ...]:
        self.requests.append(request)
        return self.output

    async def generate_video(self, request: LLMRequest) -> tuple[TextContentPart, ...]:
        self.requests.append(request)
        return self.output


class _SchedulerDelegateSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_run_local(
        self,
        intent: IntentInput,
        *,
        allow_active_run_attach: bool,
        source: InjectionSource,
    ) -> tuple[str, str]:
        self.calls.append(
            f"create:{intent.session_id}:{allow_active_run_attach}:{source.value}"
        )
        return "run-created", "session-created"

    def queue_new_run(
        self,
        *,
        session_id: str,
        intent: IntentInput,
    ) -> tuple[str, str]:
        self.calls.append(f"queue:{session_id}:{intent.intent}")
        return "run-queued", session_id

    def ensure_run_started_local(self, run_id: str) -> None:
        self.calls.append(f"ensure:{run_id}")

    def start_new_run_worker(self, run_id: str) -> None:
        self.calls.append(f"new-worker:{run_id}")

    def start_resume_worker(self, run_id: str) -> None:
        self.calls.append(f"resume-worker:{run_id}")

    def stop_run_local(self, run_id: str) -> None:
        self.calls.append(f"stop:{run_id}")


class _AuxiliaryDelegateSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_monitors(self, run_id: str) -> tuple[dict[str, object], ...]:
        self.calls.append(f"list-monitors:{run_id}")
        return ({"run_id": run_id},)

    def stop_monitor(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        self.calls.append(f"stop-monitor:{run_id}:{monitor_id}")
        return {"run_id": run_id, "monitor_id": monitor_id, "status": "stopped"}

    def get_todo(self, run_id: str) -> dict[str, object]:
        self.calls.append(f"todo:{run_id}")
        return {"run_id": run_id, "items": []}


class _InteractionDelegateSpy:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def inject_subagent_message(
        self,
        *,
        run_id: str,
        instance_id: str,
        content: str,
    ) -> None:
        self.calls.append(f"inject:{run_id}:{instance_id}:{content}")

    def persist_shell_approval_grants(
        self,
        *,
        ticket: ApprovalTicketRecord | None,
        action: str,
    ) -> None:
        ticket_id = "none" if ticket is None else ticket.tool_call_id
        self.calls.append(f"persist-shell:{ticket_id}:{action}")

    def list_user_questions(self, run_id: str) -> list[dict[str, JsonValue]]:
        self.calls.append(f"questions:{run_id}")
        return [{"run_id": run_id, "status": "pending"}]


def _media_role_registry() -> RoleRegistry:
    registry = RoleRegistry()
    registry.register(
        RoleDefinition(
            role_id="MainAgent",
            name="Main Agent",
            description="Handles direct media generation",
            version="1",
            system_prompt="Create requested media.",
        )
    )
    return registry


def _build_manager(
    db_path: Path,
    *,
    attach_manager_event_log: bool = True,
    meta_agent: object | None = None,
    provider_factory: Callable[[RoleDefinition, str | None], LLMProvider] | None = None,
    role_registry: RoleRegistry | None = None,
    media_asset_service: MediaAssetService | None = None,
    background_task_manager: BackgroundTaskManager | None = None,
    background_task_service: BackgroundTaskService | None = None,
    todo_service: TodoService | None = None,
    monitor_service: MonitorService | None = None,
) -> SessionRunService:
    control = RunControlManager()
    injection = RunInjectionManager()
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    message_repo = MessageRepository(db_path)
    event_log = EventLog(db_path)
    run_state_repo = RunStateRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    approval_ticket_repo = ApprovalTicketRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)
    shell_approval_repo = ShellApprovalRepository(db_path)
    hub = RunEventHub(event_log=event_log, run_state_repo=run_state_repo)
    active_run_registry = ActiveSessionRunRegistry(run_runtime_repo=run_runtime_repo)
    control.bind_runtime(
        run_event_hub=hub,
        injection_manager=injection,
        agent_repo=agent_repo,
        task_repo=task_repo,
        message_repo=message_repo,
        event_bus=cast(EventLog, cast(object, _EventBus())),
        run_runtime_repo=run_runtime_repo,
    )
    return SessionRunService(
        meta_agent=cast(MetaAgent, meta_agent or cast(object, _MetaAgent())),
        provider_factory=provider_factory,
        role_registry=role_registry,
        injection_manager=injection,
        run_event_hub=hub,
        run_control_manager=control,
        tool_approval_manager=ToolApprovalManager(),
        session_repo=cast(SessionRepository, cast(object, _SessionRepo())),
        active_run_registry=active_run_registry,
        event_log=event_log if attach_manager_event_log else None,
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
        approval_ticket_repo=approval_ticket_repo,
        user_question_repo=user_question_repo,
        run_runtime_repo=run_runtime_repo,
        run_intent_repo=RunIntentRepository(db_path),
        run_state_repo=run_state_repo,
        background_task_manager=background_task_manager,
        background_task_service=background_task_service,
        todo_service=todo_service,
        monitor_service=monitor_service,
        notification_service=None,
        media_asset_service=media_asset_service,
        shell_approval_repo=shell_approval_repo,
        user_question_manager=UserQuestionManager(),
    )


def _upsert_coordinator(agent_repo: AgentInstanceRepository) -> None:
    agent_repo.upsert_instance(
        run_id="run-existing",
        trace_id="run-existing",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
    )


def test_run_service_compatibility_facade_delegates_to_split_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _build_manager(tmp_path / "run_service_facade.db")
    scheduler = _SchedulerDelegateSpy()
    auxiliary = _AuxiliaryDelegateSpy()
    interactions = _InteractionDelegateSpy()
    monkeypatch.setattr(manager, "_scheduler", scheduler)
    monkeypatch.setattr(manager, "_auxiliary_service", auxiliary)
    monkeypatch.setattr(manager, "_interaction_service", interactions)

    intent = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("hello"),
    )

    assert manager._create_run_local(
        intent,
        allow_active_run_attach=False,
        source=InjectionSource.SYSTEM,
    ) == ("run-created", "session-created")
    assert manager._queue_new_run(session_id="session-1", intent=intent) == (
        "run-queued",
        "session-1",
    )
    manager._ensure_run_started_local("run-ensure")
    manager._start_new_run_worker("run-new-worker")
    manager._start_resume_worker("run-resume-worker")
    manager._stop_run_local("run-stop")

    assert manager.list_monitors("run-aux") == ({"run_id": "run-aux"},)
    assert manager.stop_monitor(run_id="run-aux", monitor_id="monitor-1") == {
        "run_id": "run-aux",
        "monitor_id": "monitor-1",
        "status": "stopped",
    }
    assert manager.get_todo("run-aux") == {"run_id": "run-aux", "items": []}

    manager.inject_subagent_message(
        run_id="run-interaction",
        instance_id="subagent-1",
        content="continue",
    )
    manager._persist_shell_approval_grants(ticket=None, action="approved")
    assert manager.list_user_questions("run-interaction") == [
        {"run_id": "run-interaction", "status": "pending"}
    ]

    assert scheduler.calls == [
        "create:session-1:False:system",
        "queue:session-1:hello",
        "ensure:run-ensure",
        "new-worker:run-new-worker",
        "resume-worker:run-resume-worker",
        "stop:run-stop",
    ]
    assert auxiliary.calls == [
        "list-monitors:run-aux",
        "stop-monitor:run-aux:monitor-1",
        "todo:run-aux",
    ]
    assert interactions.calls == [
        "inject:run-interaction:subagent-1:continue",
        "persist-shell:none:approved",
        "questions:run-interaction",
    ]


def test_get_todo_uses_run_session_from_runtime(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_todo_snapshot.db"
    manager = _build_manager(
        db_path,
        todo_service=TodoService(repository=TodoRepository(db_path)),
    )
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.IDLE,
    )

    assert manager.get_todo("run-existing") == {
        "run_id": "run-existing",
        "session_id": "session-1",
        "version": 0,
        "items": [],
        "updated_at": None,
        "updated_by_role_id": None,
        "updated_by_instance_id": None,
    }


def _upsert_instance(
    agent_repo: AgentInstanceRepository,
    *,
    instance_id: str,
    role_id: str,
    status: InstanceStatus,
    conversation_id: str | None = None,
) -> None:
    agent_repo.upsert_instance(
        run_id="run-existing",
        trace_id="run-existing",
        session_id="session-1",
        instance_id=instance_id,
        role_id=role_id,
        workspace_id="default",
        status=status,
        conversation_id=conversation_id,
    )


def _create_root_task(
    task_repo: TaskRepository,
    *,
    role_id: str = "Coordinator",
) -> None:
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-existing",
            role_id=role_id,
            objective="existing work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def _build_background_record(
    *,
    instance_id: str = "inst-worker",
    role_id: str = "writer",
) -> BackgroundTaskRecord:
    return BackgroundTaskRecord(
        background_task_id="exec-1",
        run_id="run-existing",
        session_id="session-1",
        instance_id=instance_id,
        role_id=role_id,
        tool_call_id="call-1",
        command="python worker.py",
        cwd="C:/workspace",
        execution_mode="background",
        status=BackgroundTaskStatus.COMPLETED,
        exit_code=0,
        recent_output=("done",),
        output_excerpt="done",
        log_path="tmp/background_tasks/exec-1.log",
    )


@pytest.mark.asyncio
async def test_media_generation_run_uses_native_provider(tmp_path: Path) -> None:
    provider = _NativeImageProvider()
    manager = _build_manager(
        tmp_path / "run_media_generation.db",
        provider_factory=lambda _role, _session_id: provider,
        role_registry=_media_role_registry(),
        media_asset_service=cast(MediaAssetService, object()),
    )

    result = await manager.run_intent(
        IntentInput(
            session_id="session-1",
            run_kind=RunKind.GENERATE_IMAGE,
            generation_config=ImageGenerationConfig(),
            input=content_parts_from_text("draw a compact icon"),
        )
    )

    assert result.status == "completed"
    assert result.completion_reason == RunCompletionReason.ASSISTANT_RESPONSE
    assert result.output_text == "image ready"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.run_kind == RunKind.GENERATE_IMAGE
    assert request.role_id == "MainAgent"
    assert request.prompt_text == "draw a compact icon"


@pytest.mark.asyncio
async def test_audio_generation_run_uses_native_provider(tmp_path: Path) -> None:
    provider = _NativeImageProvider()
    manager = _build_manager(
        tmp_path / "run_audio_generation.db",
        provider_factory=lambda _role, _session_id: provider,
        role_registry=_media_role_registry(),
        media_asset_service=cast(MediaAssetService, object()),
    )

    result = await manager.run_intent(
        IntentInput(
            session_id="session-1",
            run_kind=RunKind.GENERATE_AUDIO,
            generation_config=AudioGenerationConfig(),
            input=content_parts_from_text("read this aloud"),
        )
    )

    assert result.status == "completed"
    assert result.output_text == "image ready"
    assert provider.requests[0].run_kind == RunKind.GENERATE_AUDIO


@pytest.mark.asyncio
async def test_video_generation_run_uses_native_provider(tmp_path: Path) -> None:
    provider = _NativeImageProvider()
    manager = _build_manager(
        tmp_path / "run_video_generation.db",
        provider_factory=lambda _role, _session_id: provider,
        role_registry=_media_role_registry(),
        media_asset_service=cast(MediaAssetService, object()),
    )

    result = await manager.run_intent(
        IntentInput(
            session_id="session-1",
            run_kind=RunKind.GENERATE_VIDEO,
            generation_config=VideoGenerationConfig(),
            input=content_parts_from_text("animate this"),
        )
    )

    assert result.status == "completed"
    assert result.output_text == "image ready"
    assert provider.requests[0].run_kind == RunKind.GENERATE_VIDEO


@pytest.mark.asyncio
async def test_media_generation_returns_terminal_error_for_empty_output(
    tmp_path: Path,
) -> None:
    provider = _NativeImageProvider(output=())
    manager = _build_manager(
        tmp_path / "run_empty_media_generation.db",
        provider_factory=lambda _role, _session_id: provider,
        role_registry=_media_role_registry(),
        media_asset_service=cast(MediaAssetService, object()),
    )

    result = await manager.run_intent(
        IntentInput(
            session_id="session-1",
            run_kind=RunKind.GENERATE_IMAGE,
            generation_config=ImageGenerationConfig(),
            input=content_parts_from_text("draw nothing"),
        )
    )

    assert result.status == "failed"
    assert result.completion_reason == RunCompletionReason.ASSISTANT_ERROR
    assert result.error_code == "native_generation_failed"
    assert result.error_message == "Provider returned no media output"


def test_create_run_injects_into_active_run(tmp_path: Path) -> None:
    db_path = tmp_path / "run_recovery.db"
    manager = _build_manager(db_path)
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    run_id, session_id = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("follow up"),
        )
    )

    assert run_id == "run-existing"
    assert session_id == "session-1"
    queued = manager._injection_manager.drain_at_boundary("run-existing", "inst-1")
    assert len(queued) == 1
    assert queued[0].content == "follow up"


def test_create_run_updates_active_run_yolo(tmp_path: Path) -> None:
    db_path = tmp_path / "run_active_yolo.db"
    manager = _build_manager(db_path)
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))
    existing_intent = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("initial"),
        yolo=False,
    )
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    RunIntentRepository(db_path).upsert(
        run_id="run-existing",
        session_id="session-1",
        intent=existing_intent,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    run_id, session_id = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("follow up"),
            yolo=True,
        )
    )

    persisted = RunIntentRepository(db_path).get("run-existing")
    assert run_id == "run-existing"
    assert session_id == "session-1"
    assert persisted.yolo is True
    queued = manager._injection_manager.drain_at_boundary("run-existing", "inst-1")
    assert len(queued) == 1
    assert queued[0].content == "follow up"


def test_background_task_completion_enqueues_to_running_origin_instance(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_background_enqueue.db"
    manager = _build_manager(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    _upsert_coordinator(agent_repo)
    _upsert_instance(
        agent_repo,
        instance_id="inst-worker",
        role_id="writer",
        status=InstanceStatus.RUNNING,
        conversation_id="conv-worker",
    )
    _create_root_task(TaskRepository(db_path))
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    manager.handle_background_task_completion(
        record=_build_background_record(),
        message="background task finished",
    )

    queued = manager._injection_manager.drain_at_boundary(
        "run-existing",
        "inst-worker",
    )
    assert len(queued) == 1
    assert queued[0].content == "background task finished"
    assert queued[0].source == InjectionSource.SYSTEM


def test_background_task_completion_attaches_to_existing_active_run_via_create_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_background_attach_existing.db"
    manager = _build_manager(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    runtime_repo = RunRuntimeRepository(db_path)

    agent_repo.upsert_instance(
        run_id="run-existing",
        trace_id="run-existing",
        session_id="session-1",
        instance_id="inst-worker",
        role_id="writer",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
        conversation_id="conv-worker",
    )
    agent_repo.upsert_instance(
        run_id="run-newer",
        trace_id="run-newer",
        session_id="session-1",
        instance_id="inst-newer",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
        conversation_id="conv-newer",
    )
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-newer",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-newer",
            role_id="Coordinator",
            objective="newer work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    runtime_repo.ensure(
        run_id="run-newer",
        session_id="session-1",
        root_task_id="task-root-newer",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-newer",
    )
    manager._running_run_ids.add("run-newer")
    manager._injection_manager.activate("run-newer")

    manager.handle_background_task_completion(
        record=_build_background_record(),
        message="background task finished",
    )

    queued = manager._injection_manager.drain_at_boundary(
        "run-newer",
        "inst-newer",
    )
    assert len(queued) == 1
    assert queued[0].content == "background task finished"
    assert queued[0].source == InjectionSource.SYSTEM
    assert manager._active_run_registry.get_active_run_id("session-1") == "run-newer"


def test_background_task_completion_enqueues_to_running_coordinator_as_system(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_background_enqueue_coordinator.db"
    manager = _build_manager(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    _upsert_coordinator(agent_repo)
    _upsert_instance(
        agent_repo,
        instance_id="inst-worker",
        role_id="writer",
        status=InstanceStatus.COMPLETED,
        conversation_id="conv-worker",
    )
    _create_root_task(TaskRepository(db_path))
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    manager.handle_background_task_completion(
        record=_build_background_record(),
        message="background task finished",
    )

    queued = manager._injection_manager.drain_at_boundary(
        "run-existing",
        "inst-1",
    )
    assert len(queued) == 1
    assert queued[0].content == "background task finished"
    assert queued[0].source == InjectionSource.SYSTEM


@pytest.mark.asyncio
async def test_background_task_completion_keeps_source_run_active_when_siblings_remain(
    tmp_path: Path,
) -> None:
    class _BlockingMetaAgent:
        def __init__(self) -> None:
            self.intent: IntentInput | None = None
            self.trace_id: str | None = None
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle_intent(
            self,
            intent: IntentInput,
            trace_id: str | None = None,
        ) -> RunResult:
            self.intent = intent
            self.trace_id = trace_id
            self.started.set()
            await self.release.wait()
            return RunResult(
                trace_id=trace_id or "run-new",
                root_task_id="task-root-new",
                status="completed",
                output=content_parts_from_text(intent.intent),
            )

        async def resume_run(self, *, trace_id: str) -> RunResult:  # pragma: no cover
            raise AssertionError(f"not expected: {trace_id}")

    class _ListingBackgroundTaskService:
        def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
            assert run_id == "run-existing"
            return (
                _build_background_record(),
                _build_background_record().model_copy(
                    update={
                        "background_task_id": "exec-2",
                        "status": BackgroundTaskStatus.RUNNING,
                    }
                ),
            )

    db_path = tmp_path / "run_background_keep_source_active.db"
    meta_agent = _BlockingMetaAgent()
    manager = _build_manager(
        db_path,
        meta_agent=meta_agent,
        background_task_service=cast(
            BackgroundTaskService, _ListingBackgroundTaskService()
        ),
    )
    agent_repo = AgentInstanceRepository(db_path)
    _upsert_coordinator(agent_repo)
    _upsert_instance(
        agent_repo,
        instance_id="inst-worker",
        role_id="writer",
        status=InstanceStatus.COMPLETED,
        conversation_id="conv-worker",
    )
    _create_root_task(TaskRepository(db_path))
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    manager.handle_background_task_completion(
        record=_build_background_record(),
        message="background task finished",
    )
    await asyncio.wait_for(meta_agent.started.wait(), timeout=1)

    assert meta_agent.trace_id is not None
    assert meta_agent.trace_id != "run-existing"
    assert manager._active_run_registry.get_active_run_id("session-1") == "run-existing"

    meta_agent.release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_background_task_completion_starts_new_run_when_live_delivery_is_unavailable(
    tmp_path: Path,
) -> None:
    class _BlockingMetaAgent:
        def __init__(self) -> None:
            self.intent: IntentInput | None = None
            self.trace_id: str | None = None
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle_intent(
            self,
            intent: IntentInput,
            trace_id: str | None = None,
        ) -> RunResult:
            self.intent = intent
            self.trace_id = trace_id
            self.started.set()
            await self.release.wait()
            return RunResult(
                trace_id=trace_id or "run-new",
                root_task_id="task-root-new",
                status="completed",
                output=content_parts_from_text(intent.intent),
            )

        async def resume_run(self, *, trace_id: str) -> RunResult:  # pragma: no cover
            raise AssertionError(f"not expected: {trace_id}")

    db_path = tmp_path / "run_background_spawn.db"
    meta_agent = _BlockingMetaAgent()
    manager = _build_manager(db_path, meta_agent=meta_agent)
    agent_repo = AgentInstanceRepository(db_path)
    _upsert_coordinator(agent_repo)
    _upsert_instance(
        agent_repo,
        instance_id="inst-worker",
        role_id="writer",
        status=InstanceStatus.COMPLETED,
        conversation_id="conv-worker",
    )
    _create_root_task(TaskRepository(db_path))
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    manager.handle_background_task_completion(
        record=_build_background_record(),
        message="background task finished",
    )
    await asyncio.wait_for(meta_agent.started.wait(), timeout=1)

    assert meta_agent.intent is not None
    assert meta_agent.trace_id is not None
    assert meta_agent.trace_id != "run-existing"
    assert meta_agent.intent.intent == "background task finished"
    assert meta_agent.intent.target_role_id is None
    assert meta_agent.trace_id in manager._running_run_ids

    meta_agent.release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_run_service_background_task_endpoints_delegate_to_service(
    tmp_path: Path,
) -> None:
    class _CapturingBackgroundTaskService:
        def __init__(self, record: BackgroundTaskRecord) -> None:
            self.record = record
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
            self.calls.append(("list", (run_id,)))
            return (self.record,)

        def get_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            self.calls.append(("get", (run_id, background_task_id)))
            return self.record

        async def stop_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            self.calls.append(("stop", (run_id, background_task_id)))
            return self.record.model_copy(
                update={"status": BackgroundTaskStatus.STOPPED}
            )

    service = _CapturingBackgroundTaskService(_build_background_record())
    manager = _build_manager(
        tmp_path / "run_service_background_task_service.db",
        background_task_service=cast(BackgroundTaskService, service),
    )

    listed = manager.list_background_tasks("run-existing")
    fetched = manager.get_background_task(
        run_id="run-existing",
        background_task_id="exec-1",
    )
    stopped = await manager.stop_background_task(
        run_id="run-existing",
        background_task_id="exec-1",
    )

    assert [item["background_task_id"] for item in listed] == ["exec-1"]
    assert fetched["background_task_id"] == "exec-1"
    assert stopped["status"] == "stopped"
    assert service.calls == [
        ("list", ("run-existing",)),
        ("get", ("run-existing", "exec-1")),
        ("stop", ("run-existing", "exec-1")),
    ]


@pytest.mark.asyncio
async def test_run_service_background_task_endpoints_fall_back_to_manager(
    tmp_path: Path,
) -> None:
    class _CapturingBackgroundTaskManager:
        def __init__(self, record: BackgroundTaskRecord) -> None:
            self.record = record
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
            self.calls.append(("list", (run_id,)))
            return (self.record,)

        def get_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            self.calls.append(("get", (run_id, background_task_id)))
            return self.record

        async def stop_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            self.calls.append(("stop", (run_id, background_task_id)))
            return self.record.model_copy(
                update={"status": BackgroundTaskStatus.STOPPED}
            )

    task_manager = _CapturingBackgroundTaskManager(_build_background_record())
    manager = _build_manager(
        tmp_path / "run_service_background_task_manager.db",
        background_task_manager=cast(BackgroundTaskManager, task_manager),
    )

    listed = manager.list_background_tasks("run-existing")
    fetched = manager.get_background_task(
        run_id="run-existing",
        background_task_id="exec-1",
    )
    stopped = await manager.stop_background_task(
        run_id="run-existing",
        background_task_id="exec-1",
    )

    assert [item["background_task_id"] for item in listed] == ["exec-1"]
    assert fetched["background_task_id"] == "exec-1"
    assert stopped["status"] == "stopped"
    assert task_manager.calls == [
        ("list", ("run-existing",)),
        ("get", ("run-existing", "exec-1")),
        ("get", ("run-existing", "exec-1")),
        ("stop", ("run-existing", "exec-1")),
    ]


def test_create_monitor_validates_background_task_belongs_to_run(
    tmp_path: Path,
) -> None:
    class _CapturingBackgroundTaskService:
        def __init__(self, record: BackgroundTaskRecord) -> None:
            self.record = record

        def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
            _ = run_id
            return (self.record,)

        def get_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            _ = run_id
            if background_task_id != self.record.background_task_id:
                raise KeyError(background_task_id)
            return self.record

        async def stop_for_run(
            self, *, run_id: str, background_task_id: str
        ) -> BackgroundTaskRecord:
            _ = (run_id, background_task_id)
            return self.record

    class _CapturingMonitorService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def create_monitor(
            self,
            *,
            run_id: str,
            session_id: str,
            source_kind: MonitorSourceKind,
            source_key: str,
            rule: MonitorRule,
            action: MonitorAction,
            created_by_instance_id: str | None,
            created_by_role_id: str | None,
            tool_call_id: str | None,
        ) -> MonitorSubscriptionRecord:
            _ = (
                source_kind,
                rule,
                action,
                created_by_instance_id,
                created_by_role_id,
                tool_call_id,
            )
            self.calls.append((run_id, source_key))
            return MonitorSubscriptionRecord(
                monitor_id="mon_1",
                run_id=run_id,
                session_id=session_id,
                source_kind=MonitorSourceKind.BACKGROUND_TASK,
                source_key=source_key,
            )

    background_task_service = _CapturingBackgroundTaskService(
        _build_background_record()
    )
    monitor_service = _CapturingMonitorService()
    manager = _build_manager(
        tmp_path / "run_service_monitor_validation.db",
        background_task_service=cast(BackgroundTaskService, background_task_service),
        monitor_service=cast(MonitorService, monitor_service),
    )

    with pytest.raises(KeyError, match="missing-task"):
        manager.create_monitor(
            run_id="run-existing",
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="missing-task",
            rule=MonitorRule(),
            action_type=MonitorActionType.WAKE_INSTANCE,
        )

    assert monitor_service.calls == []


def test_create_run_marks_recoverable_run_for_resume(tmp_path: Path) -> None:
    db_path = tmp_path / "run_recoverable.db"
    manager = _build_manager(db_path)
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    RunRuntimeRepository(db_path).update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    run_id, session_id = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("continue from checkpoint"),
        )
    )

    assert run_id == "run-existing"
    assert session_id == "session-1"
    assert "run-existing" in manager._resume_requested_runs


def test_create_run_updates_pending_run_yolo(tmp_path: Path) -> None:
    db_path = tmp_path / "run_pending_mode.db"
    manager = _build_manager(db_path)
    pending_intent = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("initial"),
        display_input=content_parts_from_text("/time initial"),
        skills=("deepresearch",),
        yolo=False,
    )
    manager._pending_runs["run-existing"] = pending_intent
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        status=RunRuntimeStatus.QUEUED,
        phase=RunRuntimePhase.IDLE,
    )
    RunIntentRepository(db_path).upsert(
        run_id="run-existing",
        session_id="session-1",
        intent=pending_intent,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    run_id, _ = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("follow up"),
            skills=("time",),
            yolo=True,
        )
    )

    persisted = RunIntentRepository(db_path).get("run-existing")
    assert run_id == "run-existing"
    assert pending_intent.yolo is True
    assert pending_intent.display_input == ()
    assert pending_intent.intent == "initial\n\nfollow up"
    assert pending_intent.skills == ("deepresearch", "time")
    assert persisted.yolo is True
    assert persisted.display_input == ()
    assert persisted.display_intent == "initial\n\nfollow up"
    assert persisted.skills == ("deepresearch", "time")


def test_create_run_updates_recoverable_run_yolo(tmp_path: Path) -> None:
    db_path = tmp_path / "run_recoverable_mode.db"
    manager = _build_manager(db_path)
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))
    existing_intent = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("existing"),
        yolo=False,
    )
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    RunRuntimeRepository(db_path).update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    RunIntentRepository(db_path).upsert(
        run_id="run-existing",
        session_id="session-1",
        intent=existing_intent,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    run_id, _ = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("resume with yolo"),
            yolo=True,
        )
    )

    persisted = RunIntentRepository(db_path).get("run-existing")
    assert run_id == "run-existing"
    assert persisted.yolo is True


def test_create_run_blocks_when_tool_approval_pending(tmp_path: Path) -> None:
    db_path = tmp_path / "run_pending_approval.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    with pytest.raises(RuntimeError, match="waiting for tool approval"):
        manager.create_run(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("continue"),
            )
        )


def test_create_run_blocks_when_subagent_question_is_pending_in_session(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_pending_subagent_question.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    runtime_repo.ensure(
        run_id="subagent-run",
        session_id="session-1",
        root_task_id="task-root-subagent",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="question-subagent",
        run_id="subagent-run",
        session_id="session-1",
        task_id="task-root-subagent",
        instance_id="inst-subagent",
        role_id="Researcher",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Need a clarification",
                options=(
                    UserQuestionOption(
                        label="Continue",
                        description="Proceed with the current plan",
                    ),
                ),
                multiple=False,
            ),
        ),
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    with pytest.raises(RuntimeError, match="waiting for manual action"):
        manager.create_run(
            IntentInput(
                session_id="session-1",
                input=content_parts_from_text("continue"),
            )
        )


def test_create_run_ignores_orphaned_pending_question_rows_in_session(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_orphaned_subagent_question.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    user_question_repo.upsert_requested(
        question_id="question-orphaned-subagent",
        run_id="stale-subagent-run",
        session_id="session-1",
        task_id="task-root-subagent",
        instance_id="inst-subagent",
        role_id="Researcher",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Need a clarification",
                options=(
                    UserQuestionOption(
                        label="Continue",
                        description="Proceed with the current plan",
                    ),
                ),
                multiple=False,
            ),
        ),
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    run_id, session_id = manager.create_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("continue"),
        )
    )

    assert run_id == "run-existing"
    assert session_id == "session-1"
    assert "run-existing" in manager._resume_requested_runs


def test_create_detached_run_preserves_default_root_instance_reuse(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_detached.db"
    manager = _build_manager(db_path)

    run_id, session_id = manager.create_detached_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("fresh automation run"),
        )
    )

    persisted = RunIntentRepository(db_path).get(run_id)
    assert session_id == "session-1"
    assert manager._pending_runs[run_id].reuse_root_instance is True
    assert persisted.reuse_root_instance is True


def test_create_detached_run_preserves_explicit_root_instance_isolation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_detached_explicit_isolation.db"
    manager = _build_manager(db_path)

    run_id, session_id = manager.create_detached_run(
        IntentInput(
            session_id="session-1",
            input=content_parts_from_text("fresh automation run"),
            reuse_root_instance=False,
        )
    )

    persisted = RunIntentRepository(db_path).get(run_id)
    assert session_id == "session-1"
    assert manager._pending_runs[run_id].reuse_root_instance is False
    assert persisted.reuse_root_instance is False


def test_manager_hydrates_recoverable_run_from_runtime_repo(tmp_path: Path) -> None:
    db_path = tmp_path / "run_hydration.db"
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )

    manager = _build_manager(db_path)

    assert manager._active_run_registry.get_active_run_id("session-1") == "run-existing"


@pytest.mark.asyncio
async def test_resume_existing_run_uses_runtime_session_for_legacy_intent_rows(
    tmp_path: Path,
) -> None:
    class _CapturingMetaAgent:
        def __init__(self) -> None:
            self.intent: IntentInput | None = None

        async def handle_intent(
            self,
            intent: IntentInput,
            trace_id: str | None = None,
        ) -> RunResult:
            _ = trace_id
            self.intent = intent
            return RunResult(
                trace_id="run-existing",
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text(intent.intent),
            )

        async def resume_run(self, *, trace_id: str) -> RunResult:  # pragma: no cover
            raise AssertionError(f"not expected: {trace_id}")

    db_path = tmp_path / "run_resume_legacy_intent.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    intent_repo = RunIntentRepository(db_path)
    intent_repo._conn.execute(
        """
        INSERT INTO run_intents(
            run_id,
            session_id,
            intent,
            input_json,
            run_kind,
            generation_config_json,
            execution_mode,
            yolo,
            reuse_root_instance,
            thinking_enabled,
            thinking_effort,
            target_role_id,
            session_mode,
            topology_json,
            conversation_context_json,
            created_at,
            updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "run-existing",
            "None",
            "resume me",
            None,
            "conversation",
            None,
            "ai",
            "false",
            "true",
            "false",
            None,
            None,
            "normal",
            None,
            None,
            "2026-03-20T00:00:00Z",
            "2026-03-20T00:00:00Z",
        ),
    )
    intent_repo._conn.commit()
    fake_meta_agent = _CapturingMetaAgent()
    manager._meta_agent = cast(MetaAgent, cast(object, fake_meta_agent))

    result = await manager._resume_existing_run("run-existing")

    assert result.status == "completed"
    assert fake_meta_agent.intent is not None
    assert fake_meta_agent.intent.session_id == "session-1"
    assert fake_meta_agent.intent.intent == "resume me"


def test_resolve_tool_approval_requires_resume_for_stopped_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_resolve_approval.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    RunRuntimeRepository(db_path).update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    manager._tool_approval_manager.open_approval(
        run_id="run-existing",
        tool_call_id="call-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )

    with pytest.raises(
        RuntimeError, match="Resume the run before resolving tool approval"
    ):
        manager.resolve_tool_approval("run-existing", "call-1", "approve")

    ticket = ApprovalTicketRepository(db_path).get("call-1")
    assert ticket is not None
    assert ticket.status.value == "requested"


def test_stop_subagent_does_not_complete_questions_when_stop_is_rejected(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stop_subagent_rejected.db"
    manager = _build_manager(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    _upsert_coordinator(AgentInstanceRepository(db_path))
    user_question_repo.upsert_requested(
        question_id="call-question-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-1",
        instance_id="inst-1",
        role_id="Coordinator",
    )

    with pytest.raises(
        ValueError, match="Stopping coordinator via subagent API is not allowed"
    ):
        manager.stop_subagent("run-existing", "inst-1")

    record = user_question_repo.get("call-question-1")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.REQUESTED
    assert (
        question_manager.get_question(
            run_id="run-existing",
            question_id="call-question-1",
        )
        is not None
    )


def test_stop_subagent_completes_questions_and_emits_resolution_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stop_subagent_question_event.db"
    manager = _build_manager(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)

    _create_root_task(task_repo)
    run_runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _upsert_coordinator(agent_repo)
    _upsert_instance(
        agent_repo,
        instance_id="inst-2",
        role_id="Writer",
        status=InstanceStatus.RUNNING,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-2",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-2",
        role_id="Writer",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-2",
        instance_id="inst-2",
        role_id="Writer",
    )

    result = manager.stop_subagent("run-existing", "inst-2")

    assert result["instance_id"] == "inst-2"
    record = user_question_repo.get("call-question-2")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.COMPLETED
    assert (
        question_manager.get_question(
            run_id="run-existing",
            question_id="call-question-2",
        )
        is None
    )
    events = EventLog(db_path).list_by_session_with_ids("session-1")
    assert [event["event_type"] for event in events[-2:]] == [
        RunEventType.SUBAGENT_STOPPED.value,
        RunEventType.USER_QUESTION_ANSWERED.value,
    ]
    resolved_payload = json.loads(str(events[-1]["payload_json"]))
    assert resolved_payload == {
        "question_id": "call-question-2",
        "status": UserQuestionRequestStatus.COMPLETED.value,
        "instance_id": "inst-2",
        "role_id": "Writer",
    }


def test_resume_run_allows_stopped_run_with_pending_tool_approval(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_resume_pending_approval.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    RunRuntimeRepository(db_path).update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    session_id = manager.resume_run("run-existing")

    assert session_id == "session-1"
    assert "run-existing" in manager._resume_requested_runs


def test_resolve_tool_approval_persists_shell_exact_grant(tmp_path: Path) -> None:
    db_path = tmp_path / "run_shell_approval.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    workspace_key = str((tmp_path / "workspace").resolve())
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-shell-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="shell",
        args_preview='{"command":"git status"}',
        metadata={
            "workspace_key": workspace_key,
            "runtime_family": "git-bash",
            "normalized_command": "git status",
            "prefix_candidates": ["git status"],
        },
    )

    manager.resolve_tool_approval("run-existing", "call-shell-1", "approve_exact")

    shell_repo = ShellApprovalRepository(db_path)
    assert (
        shell_repo.get(
            workspace_key=workspace_key,
            runtime_family=ShellRuntimeFamily.GIT_BASH,
            scope=ShellApprovalScope.EXACT,
            value="git status",
        )
        is not None
    )


def test_resolve_tool_approval_does_not_persist_shell_grant_from_resolved_ticket(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_shell_resolved_ticket.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    workspace_key = str((tmp_path / "workspace").resolve())
    ticket_repo = ApprovalTicketRepository(db_path)
    created = ticket_repo.upsert_requested(
        tool_call_id="call-shell-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="shell",
        args_preview='{"command":"git status"}',
        metadata={
            "workspace_key": workspace_key,
            "runtime_family": "git-bash",
            "normalized_command": "git status",
            "prefix_candidates": ["git status"],
        },
    )
    ticket_repo.resolve(
        tool_call_id=created.tool_call_id,
        status=ApprovalTicketStatus.APPROVED,
    )

    with pytest.raises(RuntimeError, match="already approved"):
        manager.resolve_tool_approval("run-existing", "call-shell-1", "approve_exact")

    shell_repo = ShellApprovalRepository(db_path)
    assert (
        shell_repo.get(
            workspace_key=workspace_key,
            runtime_family=ShellRuntimeFamily.GIT_BASH,
            scope=ShellApprovalScope.EXACT,
            value="git status",
        )
        is None
    )


def test_resolve_tool_approval_rejects_cross_run_ticket_for_shell_grant(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_shell_cross_run_ticket.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    workspace_key = str((tmp_path / "workspace").resolve())
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-shell-other",
        run_id="run-other",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="shell",
        args_preview='{"command":"git status"}',
        metadata={
            "workspace_key": workspace_key,
            "runtime_family": "git-bash",
            "normalized_command": "git status",
            "prefix_candidates": ["git status"],
        },
    )

    with pytest.raises(KeyError, match="call-shell-other"):
        manager.resolve_tool_approval(
            "run-existing", "call-shell-other", "approve_exact"
        )

    shell_repo = ShellApprovalRepository(db_path)
    assert (
        shell_repo.get(
            workspace_key=workspace_key,
            runtime_family=ShellRuntimeFamily.GIT_BASH,
            scope=ShellApprovalScope.EXACT,
            value="git status",
        )
        is None
    )


def test_resolve_tool_approval_tolerates_publish_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "run_approval_publish_failure.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    manager._tool_approval_manager.open_approval(
        run_id="run-existing",
        tool_call_id="call-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )

    def raise_publish(event: RunEvent) -> None:
        del event
        raise RuntimeError("publish failed")

    setattr(manager._run_event_hub, "publish", raise_publish)

    manager.resolve_tool_approval("run-existing", "call-1", "approve")

    ticket = ApprovalTicketRepository(db_path).get("call-1")
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.APPROVED
    assert manager.list_open_tool_approvals("run-existing") == []


def test_resolve_tool_approval_returns_conflict_when_ticket_was_resolved_mid_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_approval_conflict.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    ticket_repo = ApprovalTicketRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )
    ticket_repo.upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    original_resolve = ticket_repo.resolve

    def resolve_with_conflict(*, tool_call_id: str, **kwargs: object):
        _ = kwargs
        _ = original_resolve(
            tool_call_id=tool_call_id,
            status=ApprovalTicketStatus.DENIED,
        )
        raise ApprovalTicketStatusConflictError(
            tool_call_id=tool_call_id,
            expected_status=ApprovalTicketStatus.REQUESTED,
            actual_status=ApprovalTicketStatus.DENIED,
        )

    monkeypatch.setattr(manager._approval_ticket_repo, "resolve", resolve_with_conflict)

    with pytest.raises(RuntimeError, match="already denied"):
        manager.resolve_tool_approval("run-existing", "call-1", "approve")

    ticket = ticket_repo.get("call-1")
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.DENIED


def test_resolve_tool_approval_tolerates_missing_in_memory_entry_after_persist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_approval_missing_in_memory.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    ticket_repo = ApprovalTicketRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )
    ticket_repo.upsert_requested(
        tool_call_id="call-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )
    manager._tool_approval_manager.open_approval(
        run_id="run-existing",
        tool_call_id="call-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_create_tasks",
        args_preview="{}",
    )

    def resolve_missing(**kwargs: object) -> None:
        manager._tool_approval_manager.close_approval(
            run_id="run-existing",
            tool_call_id="call-1",
        )
        raise KeyError(str(kwargs))

    monkeypatch.setattr(
        manager._tool_approval_manager, "resolve_approval", resolve_missing
    )

    manager.resolve_tool_approval("run-existing", "call-1", "approve")

    ticket = ticket_repo.get("call-1")
    assert ticket is not None
    assert ticket.status == ApprovalTicketStatus.APPROVED
    assert manager.list_open_tool_approvals("run-existing") == []


def test_resume_run_rejects_running_run(tmp_path: Path) -> None:
    db_path = tmp_path / "run_resume_running.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")

    with pytest.raises(RuntimeError, match="already running"):
        manager.resume_run("run-existing")


def test_resume_run_rejects_stopping_run(tmp_path: Path) -> None:
    db_path = tmp_path / "run_resume_stopping.db"
    manager = _build_manager(db_path)
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.STOPPING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    with pytest.raises(RuntimeError, match="is stopping"):
        manager.resume_run("run-existing")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "result_status",
        "completion_reason",
        "output_text",
        "error_message",
        "runtime_status",
        "terminal_event_type",
        "projected_status",
    ),
    [
        (
            "completed",
            RunCompletionReason.ASSISTANT_RESPONSE,
            "done",
            None,
            RunRuntimeStatus.COMPLETED,
            RunEventType.RUN_COMPLETED,
            "completed",
        ),
        (
            "failed",
            RunCompletionReason.ASSISTANT_RESPONSE,
            "",
            "Task not completed yet",
            RunRuntimeStatus.FAILED,
            RunEventType.RUN_FAILED,
            "failed",
        ),
        (
            "completed",
            RunCompletionReason.ASSISTANT_ERROR,
            "assistant error output",
            "assistant error output",
            RunRuntimeStatus.FAILED,
            RunEventType.RUN_FAILED,
            "failed",
        ),
    ],
)
async def test_worker_terminal_status_matches_run_result(
    tmp_path: Path,
    result_status: str,
    completion_reason: RunCompletionReason,
    output_text: str,
    error_message: str | None,
    runtime_status: RunRuntimeStatus,
    terminal_event_type: RunEventType,
    projected_status: str,
) -> None:
    db_path = tmp_path / f"run_worker_terminal_{result_status}.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    event_log = EventLog(db_path)
    run_state_repo = RunStateRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    async def _runner() -> RunResult:
        return RunResult(
            trace_id="run-existing",
            root_task_id="task-root-1",
            status=cast(Literal["completed", "failed"], result_status),
            completion_reason=completion_reason,
            error_message=error_message,
            output=content_parts_from_text(output_text),
        )

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=_runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == runtime_status
    assert runtime.phase == RunRuntimePhase.TERMINAL
    if runtime_status == RunRuntimeStatus.FAILED:
        assert runtime.last_error == (error_message or output_text)
    else:
        assert runtime.last_error is None

    state = run_state_repo.get_run_state("run-existing")
    assert state is not None
    assert state.status.value == projected_status
    assert state.phase.value == "terminal"
    assert state.recoverable is False

    events = event_log.list_by_session_with_ids("session-1")
    assert events[-1]["event_type"] == terminal_event_type.value


@pytest.mark.asyncio
async def test_worker_preserves_error_output_when_normalizing_failed_result(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_failed_error_output.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")
    manager._injection_manager.activate("run-existing")

    async def _runner() -> RunResult:
        return RunResult(
            trace_id="run-existing",
            root_task_id="task-root-1",
            status="failed",
            error_message="Task not completed yet",
        )

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=_runner,
    )

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    payload = json.loads(str(events[-1]["payload_json"]))
    assert str(events[-1]["event_type"]) == RunEventType.RUN_FAILED.value
    assert "Task not completed yet" in json.dumps(payload["output"])
    assert payload["error_message"] == "Task not completed yet"


@pytest.mark.asyncio
async def test_stream_run_events_replays_resume_path_after_last_seen_event(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stream_resume_replay.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")

    events_to_publish = [
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_STARTED,
            payload_json='{"session_id":"session-1"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.TEXT_DELTA,
            payload_json='{"text":"before stop"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_STOPPED,
            payload_json='{"reason":"stopped_by_user"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_RESUMED,
            payload_json='{"session_id":"session-1","reason":"resume"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.TEXT_DELTA,
            payload_json='{"text":"after resume"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_COMPLETED,
            payload_json='{"status":"completed"}',
        ),
    ]
    for event in events_to_publish:
        manager._run_event_hub.publish(event)

    replayed = [
        event
        async for event in manager.stream_run_events("run-existing", after_event_id=3)
    ]

    assert [event.event_type for event in replayed] == [
        RunEventType.RUN_RESUMED,
        RunEventType.TEXT_DELTA,
        RunEventType.RUN_COMPLETED,
    ]
    assert manager._run_event_hub.has_subscribers("run-existing") is False


@pytest.mark.asyncio
async def test_stream_run_events_stops_after_run_paused(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stream_paused.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._running_run_ids.add("run-existing")

    for event in (
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_STARTED,
            payload_json='{"session_id":"session-1"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.TEXT_DELTA,
            payload_json='{"text":"partial"}',
        ),
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_PAUSED,
            payload_json='{"error_message":"stream interrupted"}',
        ),
    ):
        manager._run_event_hub.publish(event)

    replayed = [event async for event in manager.stream_run_events("run-existing")]

    assert [event.event_type for event in replayed] == [
        RunEventType.RUN_STARTED,
        RunEventType.TEXT_DELTA,
        RunEventType.RUN_PAUSED,
    ]
    assert manager._run_event_hub.has_subscribers("run-existing") is False


@pytest.mark.asyncio
async def test_worker_auto_recovers_network_stream_interruption(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_stream_recovered.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text("recovered after stream retry"),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="network_stream_interrupted",
        error_message="stream interrupted",
        retries_used=1,
        total_attempts=3,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL
    assert manager._active_run_registry.get_active_run_id("session-1") is None

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    event_types = [str(event["event_type"]) for event in events]
    assert RunEventType.RUN_RESUMED.value in event_types
    assert event_types[-1] == RunEventType.RUN_COMPLETED.value
    assert RunEventType.RUN_PAUSED.value not in event_types

    resumed_payload = next(
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event["event_type"]) == RunEventType.RUN_RESUMED.value
    )
    assert resumed_payload["reason"] == "auto_recovery_network_stream_interrupted"
    assert resumed_payload["attempt"] == 1
    assert resumed_payload["max_attempts"] == 5

    messages = MessageRepository(db_path).get_messages_by_session("session-1")
    assert any(
        "The previous model request could not complete because of a transient network or transport failure."
        in json.dumps(message["message"], ensure_ascii=False)
        for message in messages
    )


@pytest.mark.asyncio
async def test_worker_pauses_after_stream_auto_recovery_budget_exhausted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_stream_exhausted.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    for attempt in range(1, 6):
        EventLog(db_path).emit_run_event(
            RunEvent(
                session_id="session-1",
                run_id="run-existing",
                trace_id="run-existing",
                event_type=RunEventType.RUN_RESUMED,
                payload_json=(
                    '{"session_id":"session-1","reason":"auto_recovery_network_stream_interrupted",'
                    f'"attempt":{attempt},"max_attempts":5}}'
                ),
            )
        )
    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="coordinator_agent",
        error_code="network_stream_interrupted",
        error_message="stream interrupted",
        retries_used=5,
        total_attempts=6,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_RECOVERY
    assert runtime.last_error == "stream interrupted"

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    assert events[-1]["event_type"] == RunEventType.RUN_PAUSED.value
    paused_payload = json.loads(str(events[-1]["payload_json"]))
    assert paused_payload["auto_recovery_exhausted"] is True
    assert paused_payload["attempt"] == 5
    assert paused_payload["max_attempts"] == 5
    assert (
        paused_payload["auto_recovery_reason"]
        == "auto_recovery_network_stream_interrupted"
    )


@pytest.mark.asyncio
async def test_worker_auto_recovers_network_timeout(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_timeout_recovered.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text("recovered after timeout retry"),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="network_timeout",
        error_message="timeout",
        retries_used=1,
        total_attempts=3,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    resumed_payload = next(
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event["event_type"]) == RunEventType.RUN_RESUMED.value
    )
    assert resumed_payload["reason"] == "auto_recovery_network_timeout"
    assert resumed_payload["attempt"] == 1
    assert resumed_payload["max_attempts"] == 5

    messages = MessageRepository(db_path).get_messages_by_session("session-1")
    assert any(
        "The previous model request could not complete because of a transient network or transport failure."
        in json.dumps(message["message"], ensure_ascii=False)
        for message in messages
    )


@pytest.mark.asyncio
async def test_worker_auto_recovers_transient_network_error_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_network_error_recovered.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text(
                    "recovered after transient network error"
                ),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="network_error",
        error_message="Server disconnected without sending a response",
        retries_used=1,
        total_attempts=3,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    resumed_payload = next(
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event["event_type"]) == RunEventType.RUN_RESUMED.value
    )
    assert resumed_payload["reason"] == "auto_recovery_network_error"
    assert resumed_payload["attempt"] == 1
    assert resumed_payload["max_attempts"] == 1


@pytest.mark.asyncio
async def test_worker_auto_recovers_transient_network_error_with_proxy_reset(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_network_error_proxy_recovered.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text(
                    "recovered after transient proxy network error"
                ),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="network_error",
        error_message="HTTP proxy connection reset by peer",
        retries_used=1,
        total_attempts=3,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    resumed_payload = next(
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event["event_type"]) == RunEventType.RUN_RESUMED.value
    )
    assert resumed_payload["reason"] == "auto_recovery_network_error"
    assert resumed_payload["attempt"] == 1
    assert resumed_payload["max_attempts"] == 1


@pytest.mark.asyncio
async def test_worker_does_not_auto_recover_non_transient_network_error(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_network_error_paused.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="network_error",
        error_message="Proxy authentication failed (HTTP 407)",
        retries_used=1,
        total_attempts=3,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_RECOVERY

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    assert events[-1]["event_type"] == RunEventType.RUN_PAUSED.value
    paused_payload = json.loads(str(events[-1]["payload_json"]))
    assert paused_payload["error_code"] == "network_error"
    assert "auto_recovery_reason" not in paused_payload


@pytest.mark.asyncio
async def test_worker_auto_recovers_invalid_tool_args_json_once(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_invalid_json_recovered.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text("recovered after auto resume"),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="model_tool_args_invalid_json",
        error_message="Expecting property name enclosed in double quotes",
        retries_used=0,
        total_attempts=6,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    event_types = [str(event["event_type"]) for event in events]
    assert RunEventType.RUN_RESUMED.value in event_types
    assert event_types[-1] == RunEventType.RUN_COMPLETED.value
    assert RunEventType.RUN_PAUSED.value not in event_types

    resumed_payload = next(
        json.loads(str(event["payload_json"]))
        for event in events
        if str(event["event_type"]) == RunEventType.RUN_RESUMED.value
    )
    assert resumed_payload["reason"] == "auto_recovery_invalid_tool_args_json"
    assert resumed_payload["attempt"] == 1
    assert resumed_payload["max_attempts"] == 1

    messages = MessageRepository(db_path).get_messages_by_session("session-1")
    assert any(
        "The previous tool call arguments were not valid JSON."
        in json.dumps(message["message"], ensure_ascii=False)
        for message in messages
    )


@pytest.mark.asyncio
async def test_worker_pauses_after_invalid_tool_args_auto_recovery_budget_exhausted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_invalid_json_exhausted.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    EventLog(db_path).emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-existing",
            trace_id="run-existing",
            event_type=RunEventType.RUN_RESUMED,
            payload_json='{"session_id":"session-1","reason":"auto_recovery_invalid_tool_args_json","attempt":1,"max_attempts":1}',
        )
    )
    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="model_tool_args_invalid_json",
        error_message="Expecting property name enclosed in double quotes",
        retries_used=0,
        total_attempts=6,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_RECOVERY

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    assert events[-1]["event_type"] == RunEventType.RUN_PAUSED.value
    paused_payload = json.loads(str(events[-1]["payload_json"]))
    assert paused_payload["auto_recovery_exhausted"] is True
    assert paused_payload["attempt"] == 1
    assert paused_payload["max_attempts"] == 1


@pytest.mark.asyncio
async def test_worker_targets_paused_subagent_with_auto_recovery_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_invalid_json_subagent_prompt.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    message_repo = MessageRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.SUBAGENT_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(agent_repo)
    _create_root_task(task_repo)
    agent_repo.upsert_instance(
        run_id="run-existing",
        trace_id="run-existing",
        session_id="session-1",
        instance_id="inst-2",
        role_id="time",
        workspace_id="default",
        conversation_id="session-1:time:inst-2",
        status=InstanceStatus.IDLE,
    )
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-child-1",
            session_id="session-1",
            parent_task_id="task-root-1",
            trace_id="run-existing",
            role_id="time",
            objective="child work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    task_repo.update_status(
        "task-child-1",
        TaskStatus.ASSIGNED,
        assigned_instance_id="inst-2",
    )

    class _RecoveringMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            assert trace_id == "run-existing"
            return RunResult(
                trace_id=trace_id,
                root_task_id="task-root-1",
                status="completed",
                output=content_parts_from_text("subagent recovered"),
            )

    manager._meta_agent = cast(MetaAgent, cast(object, _RecoveringMetaAgent()))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-child-1",
        session_id="session-1",
        instance_id="inst-2",
        role_id="time",
        error_code="model_tool_args_invalid_json",
        error_message="Expecting property name enclosed in double quotes",
        retries_used=0,
        total_attempts=6,
    )

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    subagent_messages = message_repo.get_messages_for_instance("session-1", "inst-2")
    coordinator_messages = message_repo.get_messages_for_instance("session-1", "inst-1")
    assert any(
        "The previous tool call arguments were not valid JSON."
        in json.dumps(message["message"], ensure_ascii=False)
        for message in subagent_messages
    )
    assert not any(
        "The previous tool call arguments were not valid JSON."
        in json.dumps(message["message"], ensure_ascii=False)
        for message in coordinator_messages
    )


@pytest.mark.asyncio
async def test_worker_caps_invalid_tool_args_auto_recovery_without_event_log(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_worker_invalid_json_no_event_log.db"
    manager = _build_manager(db_path, attach_manager_event_log=False)
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )
    _upsert_coordinator(AgentInstanceRepository(db_path))
    _create_root_task(TaskRepository(db_path))

    payload = RecoverableRunPausePayload(
        run_id="run-existing",
        trace_id="run-existing",
        task_id="task-root-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_code="model_tool_args_invalid_json",
        error_message="Expecting property name enclosed in double quotes",
        retries_used=0,
        total_attempts=6,
    )

    class _LoopingMetaAgent:
        async def handle_intent(self, intent, trace_id: str | None = None):
            raise AssertionError("not expected")

        async def resume_run(self, *, trace_id: str) -> RunResult:
            raise RecoverableRunPauseError(payload)

    manager._meta_agent = cast(MetaAgent, cast(object, _LoopingMetaAgent()))

    async def runner() -> RunResult:
        raise RecoverableRunPauseError(payload)

    await manager._worker(
        run_id="run-existing",
        session_id="session-1",
        runner=runner,
    )

    runtime = runtime_repo.get("run-existing")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == RunRuntimePhase.AWAITING_RECOVERY
    assert (
        manager._recovery_service.count_attempts(
            "run-existing",
            reason=AutoRecoveryReason.INVALID_TOOL_ARGS_JSON,
        )
        == 1
    )

    events = EventLog(db_path).list_by_session_with_ids("session-1")
    event_types = [str(event["event_type"]) for event in events]
    assert event_types.count(RunEventType.RUN_RESUMED.value) == 1
    assert event_types[-1] == RunEventType.RUN_PAUSED.value


@pytest.mark.asyncio
async def test_stream_run_events_does_not_start_pending_run_worker(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stream_no_autostart.db"
    manager = _build_manager(db_path)
    manager._pending_runs["run-existing"] = IntentInput(
        session_id="session-1",
        input=content_parts_from_text("hello"),
    )
    RunRuntimeRepository(db_path).ensure(
        run_id="run-existing",
        session_id="session-1",
        status=RunRuntimeStatus.QUEUED,
        phase=RunRuntimePhase.IDLE,
    )

    task = asyncio.create_task(
        anext(manager.stream_run_events("run-existing", after_event_id=0))
    )
    await asyncio.sleep(0)

    assert "run-existing" not in manager._running_run_ids

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_answer_user_question_validates_payload_and_auto_resumes_stopped_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick labels",
                options=(
                    UserQuestionOption(label="A", description="Option A"),
                    UserQuestionOption(label="B", description="Option B"),
                ),
                multiple=True,
            ),
            UserQuestionPrompt(
                question="Pick fallback",
                options=(UserQuestionOption(label="Default", description="Default"),),
                multiple=False,
                placeholder="Add details",
            ),
        ),
    )
    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="run-existing",
        question_id="call-question-1",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "A"},
                            {"label": "B", "supplement": "Need both paths"},
                        ]
                    },
                    {
                        "selections": [
                            {
                                "label": "__none_of_the_above__",
                                "supplement": "Need both paths",
                            }
                        ]
                    },
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert "run-existing" in manager._resume_requested_runs
    assert ensured == ["run-existing"]
    record = user_question_repo.get("call-question-1")
    assert record is not None
    assert record.status.value == "answered"


def test_answer_user_question_skips_resume_when_session_has_other_pending_question(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_pending_session.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    user_question_repo.upsert_requested(
        question_id="call-question-2",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick another",
                options=(UserQuestionOption(label="Later", description="Later"),),
                multiple=False,
            ),
        ),
    )

    resume_calls: list[str] = []
    ensured: list[str] = []
    manager.resume_run = lambda run_id: resume_calls.append(run_id) or "session-1"
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="run-existing",
        question_id="call-question-1",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert resume_calls == []
    assert ensured == []
    answered_record = user_question_repo.get("call-question-1")
    assert answered_record is not None
    assert answered_record.status.value == "answered"
    pending_record = user_question_repo.get("call-question-2")
    assert pending_record is not None
    assert pending_record.status.value == "requested"


def test_answer_user_question_ignores_orphaned_pending_session_question_for_resume(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_orphaned_session.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-1",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    user_question_repo.upsert_requested(
        question_id="call-question-orphaned",
        run_id="run-missing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-missing",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Missing run question",
                options=(UserQuestionOption(label="Ignore", description="Ignore"),),
                multiple=False,
            ),
        ),
    )

    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="run-existing",
        question_id="call-question-1",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert "run-existing" in manager._resume_requested_runs
    assert ensured == ["run-existing"]
    answered_record = user_question_repo.get("call-question-1")
    assert answered_record is not None
    assert answered_record.status.value == "answered"
    orphaned_record = user_question_repo.get("call-question-orphaned")
    assert orphaned_record is not None
    assert orphaned_record.status.value == "requested"


def test_answer_user_question_skips_resume_when_run_becomes_running_during_submit(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_running_race.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="subagent_run_sync123",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "subagent_run_sync123",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-race",
        run_id="subagent_run_sync123",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-subagent",
        role_id="Explorer",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    original_runtime_for_run = manager._runtime_for_run
    call_count = 0

    def runtime_for_run(run_id: str):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            runtime_repo.update(
                "subagent_run_sync123",
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.SUBAGENT_RUNNING,
            )
        return original_runtime_for_run(run_id)

    setattr(manager, "_runtime_for_run", runtime_for_run)
    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="subagent_run_sync123",
        question_id="call-question-race",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert ensured == []
    assert "subagent_run_sync123" not in manager._resume_requested_runs
    record = user_question_repo.get("call-question-race")
    assert record is not None
    assert record.status.value == "answered"


def test_answer_user_question_skips_resume_when_agent_is_still_running(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_running_agent.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)
    agent_repo = AgentInstanceRepository(db_path)

    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-subagent",
            session_id="session-1",
            parent_task_id=None,
            trace_id="subagent_run_sync123",
            role_id="Explorer",
            objective="subagent work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo.upsert_instance(
        run_id="subagent_run_sync123",
        trace_id="subagent_run_sync123",
        session_id="session-1",
        instance_id="inst-subagent",
        role_id="Explorer",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
    )
    runtime_repo.ensure(
        run_id="subagent_run_sync123",
        session_id="session-1",
        root_task_id="task-root-subagent",
    )
    runtime_repo.update(
        "subagent_run_sync123",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-running-agent",
        run_id="subagent_run_sync123",
        session_id="session-1",
        task_id="task-root-subagent",
        instance_id="inst-subagent",
        role_id="Explorer",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )

    resume_calls: list[str] = []
    ensured: list[str] = []
    manager.resume_run = lambda run_id: resume_calls.append(run_id) or "session-1"
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="subagent_run_sync123",
        question_id="call-question-running-agent",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert resume_calls == []
    assert ensured == []
    record = user_question_repo.get("call-question-running-agent")
    assert record is not None
    assert record.status.value == "answered"


def test_answer_user_question_tolerates_publish_failure_and_still_resumes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_publish_failure.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-publish-failure",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )

    def raise_publish(event: RunEvent) -> None:
        del event
        raise RuntimeError("publish failed")

    setattr(manager._run_event_hub, "publish", raise_publish)
    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="run-existing",
        question_id="call-question-publish-failure",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert "run-existing" in manager._resume_requested_runs
    assert ensured == ["run-existing"]
    record = user_question_repo.get("call-question-publish-failure")
    assert record is not None
    assert record.status.value == "answered"


def test_answer_user_question_tolerates_closed_manager_entry_after_persist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_closed_manager.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-closed-manager",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-closed-manager",
        instance_id="inst-1",
        role_id="Coordinator",
    )

    def resolve_missing(**kwargs: object) -> None:
        question_manager.close_question(
            run_id="run-existing",
            question_id="call-question-closed-manager",
            reason="stopped",
        )
        raise UserQuestionClosedError(str(kwargs))

    monkeypatch.setattr(question_manager, "resolve_question", resolve_missing)
    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    result = manager.answer_user_question(
        run_id="run-existing",
        question_id="call-question-closed-manager",
        answers=UserQuestionAnswerSubmission.model_validate(
            {
                "answers": [
                    {
                        "selections": [
                            {"label": "Only"},
                        ]
                    }
                ]
            }
        ),
    )

    assert result["status"] == "answered"
    assert "run-existing" in manager._resume_requested_runs
    assert ensured == ["run-existing"]
    record = user_question_repo.get("call-question-closed-manager")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.ANSWERED


def test_answer_user_question_returns_conflict_when_question_was_completed_mid_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_completed_race.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-completed-race",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    original_resolve = user_question_repo.resolve

    def resolve_with_conflict(*, question_id: str, **kwargs: object):
        _ = kwargs
        _ = original_resolve(
            question_id=question_id,
            status=UserQuestionRequestStatus.COMPLETED,
        )
        raise UserQuestionStatusConflictError(
            question_id=question_id,
            expected_status=UserQuestionRequestStatus.REQUESTED,
            actual_status=UserQuestionRequestStatus.COMPLETED,
        )

    manager_repo = manager._user_question_repo
    assert manager_repo is not None
    monkeypatch.setattr(manager_repo, "resolve", resolve_with_conflict)
    ensured: list[str] = []
    manager.ensure_run_started = lambda run_id: ensured.append(run_id)

    with pytest.raises(RuntimeError, match="was already completed"):
        manager.answer_user_question(
            run_id="run-existing",
            question_id="call-question-completed-race",
            answers=UserQuestionAnswerSubmission.model_validate(
                {
                    "answers": [
                        {
                            "selections": [
                                {"label": "Only"},
                            ]
                        }
                    ]
                }
            ),
        )

    assert ensured == []
    assert "run-existing" not in manager._resume_requested_runs
    record = user_question_repo.get("call-question-completed-race")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.COMPLETED


def test_answer_user_question_rejects_invalid_multiple_choice(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_invalid.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-2",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only option"),),
                multiple=False,
            ),
        ),
    )

    with pytest.raises(ValueError, match="does not allow multiple choices"):
        manager.answer_user_question(
            run_id="run-existing",
            question_id="call-question-2",
            answers=UserQuestionAnswerSubmission.model_validate(
                {
                    "answers": [
                        {
                            "selections": [
                                {"label": "Only"},
                                {"label": "Extra"},
                            ]
                        }
                    ]
                }
            ),
        )


def test_answer_user_question_rejects_none_of_the_above_with_other_options(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_answer_user_question_none_conflict.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-existing",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-3",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only option"),),
                multiple=True,
            ),
        ),
    )

    with pytest.raises(ValueError, match="cannot combine None of the above"):
        manager.answer_user_question(
            run_id="run-existing",
            question_id="call-question-3",
            answers=UserQuestionAnswerSubmission.model_validate(
                {
                    "answers": [
                        {
                            "selections": [
                                {"label": "Only"},
                                {"label": "__none_of_the_above__"},
                            ]
                        }
                    ]
                }
            ),
        )


def test_stop_run_completes_pending_user_questions_and_closes_manager_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stop_user_questions.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-stop",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-stop",
        instance_id="inst-1",
        role_id="Coordinator",
    )
    manager._running_run_ids.add("run-existing")
    monkeypatch.setattr(
        manager._run_control_manager, "request_run_stop", lambda _run_id: True
    )

    manager.stop_run("run-existing")

    record = user_question_repo.get("call-question-stop")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.COMPLETED
    assert (
        question_manager.get_question(
            run_id="run-existing",
            question_id="call-question-stop",
        )
        is None
    )
    assert user_question_repo.list_by_run("run-existing") == ()


def test_stop_run_does_not_complete_questions_when_stop_request_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stop_user_questions_request_failed.db"
    manager = _build_manager(db_path)
    runtime_repo = RunRuntimeRepository(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    runtime_repo.ensure(
        run_id="run-existing",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_MANUAL_ACTION,
    )
    user_question_repo.upsert_requested(
        question_id="call-question-stop-failed",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-stop-failed",
        instance_id="inst-1",
        role_id="Coordinator",
    )
    monkeypatch.setattr(
        manager._run_control_manager, "request_run_stop", lambda _run_id: False
    )

    with pytest.raises(KeyError, match="Run run-existing not found"):
        manager.stop_run("run-existing")

    record = user_question_repo.get("call-question-stop-failed")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.REQUESTED
    assert (
        question_manager.get_question(
            run_id="run-existing",
            question_id="call-question-stop-failed",
        )
        is not None
    )


def test_complete_pending_user_questions_does_not_overwrite_answered_race(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_stop_user_questions_answered_race.db"
    manager = _build_manager(db_path)
    task_repo = TaskRepository(db_path)
    user_question_repo = UserQuestionRepository(db_path)

    _create_root_task(task_repo)
    user_question_repo.upsert_requested(
        question_id="call-question-stop-race",
        run_id="run-existing",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="ask_question",
        questions=(
            UserQuestionPrompt(
                question="Pick one",
                options=(UserQuestionOption(label="Only", description="Only"),),
                multiple=False,
            ),
        ),
    )
    question_manager = manager._user_question_manager
    assert question_manager is not None
    question_manager.open_question(
        run_id="run-existing",
        question_id="call-question-stop-race",
        instance_id="inst-1",
        role_id="Coordinator",
    )
    original_resolve = user_question_repo.resolve

    def resolve_with_answered_race(
        *,
        question_id: str,
        status: UserQuestionRequestStatus,
        answers: tuple[UserQuestionAnswer, ...] = (),
        expected_status: UserQuestionRequestStatus | None = None,
    ):
        if (
            question_id == "call-question-stop-race"
            and status == UserQuestionRequestStatus.COMPLETED
        ):
            _ = original_resolve(
                question_id=question_id,
                status=UserQuestionRequestStatus.ANSWERED,
                answers=(
                    UserQuestionAnswer(
                        selections=(UserQuestionSelection(label="Only"),),
                    ),
                ),
            )
            raise UserQuestionStatusConflictError(
                question_id=question_id,
                expected_status=UserQuestionRequestStatus.REQUESTED,
                actual_status=UserQuestionRequestStatus.ANSWERED,
            )
        return original_resolve(
            question_id=question_id,
            status=status,
            answers=answers,
            expected_status=expected_status,
        )

    manager_repo = manager._user_question_repo
    assert manager_repo is not None
    monkeypatch.setattr(manager_repo, "resolve", resolve_with_answered_race)

    manager._complete_pending_user_questions(
        run_id="run-existing",
        reason="run_stopped",
    )

    record = user_question_repo.get("call-question-stop-race")
    assert record is not None
    assert record.status == UserQuestionRequestStatus.ANSWERED
    assert (
        question_manager.get_question(
            run_id="run-existing",
            question_id="call-question-stop-race",
        )
        is None
    )
