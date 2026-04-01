# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal, cast

import pytest

from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.media import content_parts_from_text
from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.run_control_manager import RunControlManager
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.run_manager import AutoRecoveryReason, RunManager
from agent_teams.sessions.runs.run_models import IntentInput, RunEvent, RunResult
from agent_teams.sessions.runs.assistant_errors import RunCompletionReason
from agent_teams.sessions.runs.recoverable_pause import (
    RecoverableRunPauseError,
    RecoverableRunPausePayload,
)
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.runs.run_state_repo import RunStateRepository
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


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


def _build_manager(
    db_path: Path, *, attach_manager_event_log: bool = True
) -> RunManager:
    control = RunControlManager()
    injection = RunInjectionManager()
    agent_repo = AgentInstanceRepository(db_path)
    task_repo = TaskRepository(db_path)
    message_repo = MessageRepository(db_path)
    event_log = EventLog(db_path)
    run_state_repo = RunStateRepository(db_path)
    run_runtime_repo = RunRuntimeRepository(db_path)
    approval_ticket_repo = ApprovalTicketRepository(db_path)
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
    return RunManager(
        meta_agent=cast(MetaAgent, cast(object, _MetaAgent())),
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
        run_runtime_repo=run_runtime_repo,
        run_intent_repo=RunIntentRepository(db_path),
        run_state_repo=run_state_repo,
        notification_service=None,
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
            yolo=True,
        )
    )

    persisted = RunIntentRepository(db_path).get("run-existing")
    assert run_id == "run-existing"
    assert pending_intent.yolo is True
    assert persisted.yolo is True


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
        tool_name="create_tasks",
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
        tool_name="create_tasks",
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
        tool_name="create_tasks",
        args_preview="{}",
    )

    with pytest.raises(
        RuntimeError, match="Resume the run before resolving tool approval"
    ):
        manager.resolve_tool_approval("run-existing", "call-1", "approve")

    ticket = ApprovalTicketRepository(db_path).get("call-1")
    assert ticket is not None
    assert ticket.status.value == "requested"


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
        tool_name="create_tasks",
        args_preview="{}",
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    session_id = manager.resume_run("run-existing")

    assert session_id == "session-1"
    assert "run-existing" in manager._resume_requested_runs


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
        "The previous model stream was interrupted by a transient network or transport failure."
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
        manager._auto_recovery_attempts[
            ("run-existing", AutoRecoveryReason.INVALID_TOOL_ARGS_JSON)
        ]
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
