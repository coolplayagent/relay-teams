from __future__ import annotations

from pathlib import Path

from agent_teams.sessions.service import SessionService
from agent_teams.state.agent_repo import AgentInstanceRepository
from agent_teams.state.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.state.event_log import EventLog
from agent_teams.state.message_repo import MessageRepository
from agent_teams.state.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.state.session_repo import SessionRepository
from agent_teams.state.task_repo import TaskRepository
from agent_teams.state.token_usage_repo import TokenUsageRepository
from agent_teams.workflow.models import TaskEnvelope, VerificationPlan


def _build_service(db_path: Path) -> SessionService:
    return SessionService(
        session_repo=SessionRepository(db_path),
        task_repo=TaskRepository(db_path),
        agent_repo=AgentInstanceRepository(db_path),
        message_repo=MessageRepository(db_path),
        approval_ticket_repo=ApprovalTicketRepository(db_path),
        run_runtime_repo=RunRuntimeRepository(db_path),
        token_usage_repo=TokenUsageRepository(db_path),
        run_event_hub=None,
        event_log=EventLog(db_path),
    )


def _seed_root_task(db_path: Path, *, run_id: str, session_id: str) -> None:
    _ = TaskRepository(db_path).create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id=session_id,
            parent_task_id=None,
            trace_id=run_id,
            objective="do work",
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
    )


def test_list_sessions_includes_active_run_overlay(tmp_path: Path) -> None:
    db_path = tmp_path / "session_list_status.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active")
    _ = service.create_session(session_id="session-idle")

    _seed_root_task(db_path, run_id="run-active", session_id="session-active")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-active",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="dispatch_task:1",
        run_id="run-active",
        session_id="session-active",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="coordinator_agent",
        tool_name="dispatch_task",
        args_preview='{"task_id":"task-1"}',
    )

    sessions = service.list_sessions()
    by_id = {record.session_id: record for record in sessions}

    active = by_id["session-active"]
    assert active.has_active_run is True
    assert active.active_run_id == "run-active"
    assert active.active_run_status == "paused"
    assert active.active_run_phase == "awaiting_tool_approval"
    assert active.pending_tool_approval_count == 1

    idle = by_id["session-idle"]
    assert idle.has_active_run is False
    assert idle.active_run_id is None
    assert idle.active_run_status is None
    assert idle.active_run_phase is None
    assert idle.pending_tool_approval_count == 0


def test_list_sessions_uses_runtime_overlay_for_running_subagent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_subagent_status.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active")

    _seed_root_task(db_path, run_id="run-active", session_id="session-active")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-active",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_SUBAGENT_FOLLOWUP,
        active_instance_id="inst-sub-1",
        active_task_id="task-root-1",
        active_role_id="time",
        active_subagent_instance_id="inst-sub-1",
    )

    sessions = service.list_sessions()
    active = {record.session_id: record for record in sessions}["session-active"]

    assert active.has_active_run is True
    assert active.active_run_id == "run-active"
    assert active.active_run_status == "paused"
    assert active.active_run_phase == "awaiting_subagent_followup"
    assert active.pending_tool_approval_count == 0
