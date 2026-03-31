from __future__ import annotations

from pathlib import Path

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

from agent_teams.agents.instances.enums import InstanceStatus
from agent_teams.sessions.runs.active_run_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.sessions.session_service import SessionService
from agent_teams.agents.instances.instance_repository import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.sessions.runs.run_state_repo import RunStateRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.session_repository import SessionRepository
from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.providers.token_usage_repo import TokenUsageRepository
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


def _build_service(
    db_path: Path,
    *,
    run_event_hub: RunEventHub | None = None,
    active_run_registry: ActiveSessionRunRegistry | None = None,
) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_state_repo=RunStateRepository(db_path),
        run_event_hub=run_event_hub,
        active_run_registry=active_run_registry,
        event_log=EventLog(db_path),
    )


def _seed_root_task(
    db_path: Path,
    *,
    run_id: str,
    session_id: str,
    role_id: str = "Coordinator",
) -> None:
    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            role_id=role_id,
            objective="do work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def _seed_delegated_task(
    db_path: Path,
    *,
    run_id: str,
    session_id: str,
    task_id: str,
) -> None:
    task_repo = TaskRepository(db_path)
    _ = task_repo.create(
        TaskEnvelope(
            task_id=task_id,
            session_id=session_id,
            parent_task_id="task-root-1",
            trace_id=run_id,
            role_id="time",
            title="Ask time",
            objective="ask the current time",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def test_get_recovery_snapshot_returns_active_run_and_pause_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery.db"
    hub = RunEventHub()
    service = _build_service(db_path, run_event_hub=hub)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-active",
        trace_id="run-active",
        session_id="session-1",
        instance_id="inst-2",
        role_id="spec_coder",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
    )
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        active_instance_id="inst-2",
        active_task_id="task-root-1",
        active_role_id="spec_coder",
        active_subagent_instance_id="inst-2",
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("run_id") == "run-active"
    assert active_run.get("is_recoverable") is True
    assert active_run.get("stream_connected") is False
    assert active_run.get("should_show_recover") is True
    assert active_run.get("phase") == "awaiting_subagent_followup"
    assert active_run.get("pending_tool_approval_count") == 0
    assert active_run.get("primary_role_id") == "Coordinator"

    paused_subagent = snapshot.get("paused_subagent")
    assert isinstance(paused_subagent, dict)
    assert paused_subagent.get("instance_id") == "inst-2"
    assert paused_subagent.get("role_id") == "spec_coder"

    round_snapshot = snapshot.get("round_snapshot")
    assert isinstance(round_snapshot, dict)
    assert round_snapshot.get("run_id") == "run-active"
    assert round_snapshot.get("primary_role_id") == "Coordinator"


def test_get_recovery_snapshot_exposes_awaiting_recovery_phase(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_awaiting_recovery.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_RECOVERY,
        last_error="stream interrupted",
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("status") == "paused"
    assert active_run.get("phase") == "awaiting_recovery"
    assert active_run.get("is_recoverable") is True
    assert active_run.get("should_show_recover") is True


def test_get_recovery_snapshot_marks_connected_stream_without_recover_button(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_connected.db"
    hub = RunEventHub()
    service = _build_service(db_path, run_event_hub=hub)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = hub.subscribe("run-active")

    snapshot = service.get_recovery_snapshot("session-1")
    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("stream_connected") is True
    assert active_run.get("is_recoverable") is True
    assert active_run.get("should_show_recover") is False
    assert active_run.get("phase") == "running"
    assert active_run.get("pending_tool_approval_count") == 0


def test_get_recovery_snapshot_does_not_auto_stream_interrupted_running_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_interrupted_running.db"
    runtime_repo = RunRuntimeRepository(db_path)
    _ = SessionRepository(db_path).create(
        session_id="session-1",
        workspace_id="default",
    )
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = runtime_repo.mark_transient_runs_interrupted()

    service = _build_service(db_path)
    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("run_id") == "run-active"
    assert active_run.get("status") == "stopped"
    assert active_run.get("phase") == "stopped"
    assert active_run.get("stream_connected") is False
    assert active_run.get("should_show_recover") is True


def test_get_recovery_snapshot_exposes_stopping_run_without_recover_button(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_stopping.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.STOPPING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("status") == "stopping"
    assert active_run.get("phase") == "stopping"
    assert active_run.get("is_recoverable") is False
    assert active_run.get("should_show_recover") is False


def test_get_recovery_snapshot_includes_stream_event_offsets(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery_offsets.db"
    hub = RunEventHub(
        event_log=EventLog(db_path),
        run_state_repo=RunStateRepository(db_path),
    )
    service = _build_service(db_path, run_event_hub=hub)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    hub.publish(
        RunEvent(
            session_id="session-1",
            run_id="run-active",
            trace_id="run-active",
            event_type=RunEventType.RUN_STARTED,
            payload_json='{"session_id":"session-1"}',
        )
    )
    hub.publish(
        RunEvent(
            session_id="session-1",
            run_id="run-active",
            trace_id="run-active",
            event_type=RunEventType.TEXT_DELTA,
            payload_json='{"text":"hi"}',
        )
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("checkpoint_event_id") == 1
    assert active_run.get("last_event_id") == 2


def test_get_recovery_snapshot_uses_runtime_active_run_when_events_not_written(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_runtime_active.db"
    active_run_registry = ActiveSessionRunRegistry()
    active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-runtime-active",
    )
    service = _build_service(
        db_path,
        active_run_registry=active_run_registry,
    )
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-runtime-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-runtime-active",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.QUEUED,
        phase=RunRuntimePhase.IDLE,
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("run_id") == "run-runtime-active"
    assert active_run.get("status") == "queued"
    assert active_run.get("is_recoverable") is True
    assert active_run.get("stream_connected") is False
    assert active_run.get("should_show_recover") is True
    assert active_run.get("phase") == "queued"
    assert active_run.get("pending_tool_approval_count") == 0


def test_get_recovery_snapshot_prefers_approval_phase(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery_approval.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    approval_repo = ApprovalTicketRepository(db_path)
    approval_repo.upsert_requested(
        tool_call_id="call-1",
        run_id="run-active",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="dispatch_task",
        args_preview='{"task_id":"task-1"}',
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("phase") == "awaiting_tool_approval"
    assert active_run.get("pending_tool_approval_count") == 1
    pending = snapshot.get("pending_tool_approvals")
    assert isinstance(pending, list)
    assert len(pending) == 1
    first_pending = pending[0]
    assert isinstance(first_pending, dict)
    assert first_pending.get("tool_call_id") == "call-1"


def test_get_recovery_snapshot_keeps_approval_phase_for_stopped_recoverable_run(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_stopped_approval.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )
    approval_repo = ApprovalTicketRepository(db_path)
    approval_repo.upsert_requested(
        tool_call_id="call-1",
        run_id="run-active",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="dispatch_task",
        args_preview='{"task_id":"task-1"}',
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("status") == "stopped"
    assert active_run.get("phase") == "awaiting_tool_approval"
    assert active_run.get("pending_tool_approval_count") == 1


def test_get_recovery_snapshot_marks_started_main_agent_stop_as_recoverable(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_main_agent_stopped.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(
        db_path,
        run_id="run-active",
        session_id="session-1",
        role_id="MainAgent",
    )
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.STOPPED,
        phase=RunRuntimePhase.IDLE,
    )

    snapshot = service.get_recovery_snapshot("session-1")

    active_run = snapshot.get("active_run")
    assert isinstance(active_run, dict)
    assert active_run.get("status") == "stopped"
    assert active_run.get("is_recoverable") is True
    assert active_run.get("should_show_recover") is True

    round_snapshot = snapshot.get("round_snapshot")
    assert isinstance(round_snapshot, dict)
    assert round_snapshot.get("is_recoverable") is True
    assert round_snapshot.get("primary_role_id") == "MainAgent"


def test_get_recovery_snapshot_round_snapshot_keeps_task_summaries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_graph.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    _seed_delegated_task(
        db_path,
        run_id="run-active",
        session_id="session-1",
        task_id="task-sub-1",
    )
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    snapshot = service.get_recovery_snapshot("session-1")
    round_snapshot = snapshot.get("round_snapshot")
    assert isinstance(round_snapshot, dict)
    tasks = round_snapshot.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "task-sub-1"
    assert tasks[0]["role_id"] == "time"


def test_get_recovery_snapshot_ignores_main_agent_paused_subagent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_main_agent_pause.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-active", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        active_instance_id="inst-main",
        active_task_id="task-root-1",
        active_role_id="MainAgent",
        active_subagent_instance_id="inst-main",
    )

    snapshot = service.get_recovery_snapshot("session-1")

    assert snapshot.get("paused_subagent") is None


def test_get_recovery_snapshot_round_snapshot_keeps_tool_results(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_tool_results.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = TaskRepository(db_path).create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-active",
            role_id="Coordinator",
            objective="recover tool results",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )
    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-active",
        trace_id="run-active",
        session_id="session-1",
        instance_id="inst-coordinator",
        role_id="Coordinator",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
    )
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-1",
        root_task_id="task-root-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    MessageRepository(db_path).append(
        session_id="session-1",
        workspace_id="default",
        instance_id="inst-coordinator",
        task_id="task-root-1",
        trace_id="run-active",
        agent_role_id="Coordinator",
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="list_available_roles",
                        args={},
                        tool_call_id="call-1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="list_available_roles",
                        tool_call_id="call-1",
                        content={"ok": True, "data": {"roles": ["time"]}},
                    )
                ]
            ),
        ],
    )

    snapshot = service.get_recovery_snapshot("session-1")

    round_snapshot = snapshot.get("round_snapshot")
    assert isinstance(round_snapshot, dict)
    coordinator_messages = round_snapshot.get("coordinator_messages")
    assert isinstance(coordinator_messages, list)
    assert len(coordinator_messages) == 2
    assert coordinator_messages[1]["message"]["parts"][0]["part_kind"] == "tool-return"


def test_failed_terminal_run_is_exposed_through_round_projection_not_recovery(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "recovery_failed.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-1", workspace_id="default")
    _seed_root_task(db_path, run_id="run-failed", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-failed",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-failed",
        status=RunRuntimeStatus.FAILED,
        phase=RunRuntimePhase.TERMINAL,
        last_error="Task not completed yet",
    )

    snapshot = service.get_recovery_snapshot("session-1")
    assert snapshot.get("active_run") is None

    round_snapshot = service.get_round("session-1", "run-failed")
    assert round_snapshot["run_id"] == "run-failed"
    assert round_snapshot["run_status"] == "failed"
    assert round_snapshot["run_phase"] == "failed"
    assert round_snapshot["is_recoverable"] is False
