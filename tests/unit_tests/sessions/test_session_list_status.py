from __future__ import annotations

from datetime import datetime, timezone
from json import dumps
from pathlib import Path
import sqlite3

import pytest

from relay_teams.sessions.session_service import SessionService
from relay_teams.agent_runtimes.instances.instance_repository import (
    AgentInstanceRepository,
)
from relay_teams.agent_runtimes.instances.enums import (
    InstanceLifecycle,
    InstanceStatus,
)
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan


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
    _ = service.create_session(session_id="session-active", workspace_id="default")
    _ = service.create_session(session_id="session-idle", workspace_id="default")

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
        tool_call_id="orch_dispatch_task:1",
        run_id="run-active",
        session_id="session-active",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_dispatch_task",
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


def test_get_session_uses_fresh_list_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "session_get_cache.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active", workspace_id="default")
    _ = service.list_sessions()

    def _fail_get(session_id: str) -> object:
        raise AssertionError(f"unexpected repository get for {session_id}")

    monkeypatch.setattr(service._session_repo, "get", _fail_get)

    record = service.get_session("session-active")

    assert record.session_id == "session-active"


def test_get_session_includes_subagent_session_count(tmp_path: Path) -> None:
    db_path = tmp_path / "session_get_subagent_count.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-root",
        trace_id="run-root",
        session_id="session-1",
        instance_id="main",
        role_id="MainAgent",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
        lifecycle=InstanceLifecycle.REUSABLE,
        parent_instance_id=None,
    )
    agent_repo.upsert_instance(
        run_id="run-sub-1",
        trace_id="run-sub-1",
        session_id="session-1",
        instance_id="sub-1",
        role_id="Explorer",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
        lifecycle=InstanceLifecycle.EPHEMERAL,
        parent_instance_id="main",
    )
    agent_repo.upsert_instance(
        run_id="run-sub-2",
        trace_id="run-sub-2",
        session_id="session-1",
        instance_id="sub-2",
        role_id="Explorer",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
        lifecycle=InstanceLifecycle.EPHEMERAL,
        parent_instance_id="main",
    )

    record = service.get_session("session-1")

    assert record.subagent_session_count == 2


@pytest.mark.asyncio
async def test_get_session_async_includes_subagent_session_count(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_get_subagent_count_async.db"
    service = _build_service(db_path)
    _ = await service.create_session_async(
        session_id="session-1",
        workspace_id="default",
    )
    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-sub-1",
        trace_id="run-sub-1",
        session_id="session-1",
        instance_id="sub-1",
        role_id="Explorer",
        workspace_id="default",
        status=InstanceStatus.COMPLETED,
        lifecycle=InstanceLifecycle.EPHEMERAL,
        parent_instance_id="main",
    )

    record = await service.get_session_async("session-1")

    assert record.subagent_session_count == 1


def test_spawn_subagent_tool_event_invalidates_session_count_list_cache(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_subagent_count_dirty.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    cached_sessions = service.list_sessions()
    assert cached_sessions[0].subagent_session_count == 0

    agent_repo = AgentInstanceRepository(db_path)
    agent_repo.upsert_instance(
        run_id="run-sub-1",
        trace_id="run-root",
        session_id="session-1",
        instance_id="sub-1",
        role_id="Explorer",
        workspace_id="default",
        status=InstanceStatus.RUNNING,
        lifecycle=InstanceLifecycle.EPHEMERAL,
        parent_instance_id="main",
    )

    service._observe_run_event_for_snapshot_dirty(
        RunEvent(
            session_id="session-1",
            run_id="run-root",
            trace_id="run-root",
            event_type=RunEventType.TOOL_RESULT,
            payload_json=dumps({"tool_name": "spawn_subagent"}),
        )
    )

    sessions = service.list_sessions()

    assert sessions[0].subagent_session_count == 1


def test_create_session_seeds_fast_recovery_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "session_recovery_seed.db"
    service = _build_service(db_path)

    _ = service.create_session(session_id="session-seeded", workspace_id="default")
    snapshot = service.get_fast_cached_recovery_snapshot("session-seeded")

    assert snapshot is not None
    assert snapshot["active_run"] is None
    assert snapshot["snapshot_cache_hit"] is True


def test_delete_session_removes_record_from_stale_list_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "session_delete_list_cache.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-delete", workspace_id="default")
    _ = service.list_sessions()

    service.delete_session("session-delete")
    sessions = service.list_sessions()

    assert [record.session_id for record in sessions] == []


def test_terminal_viewed_updates_record_in_stale_list_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "session_terminal_viewed_list_cache.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-terminal", workspace_id="default")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-terminal",
        session_id="session-terminal",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-terminal",
        status=RunRuntimeStatus.COMPLETED,
        phase=RunRuntimePhase.TERMINAL,
    )
    _ = service.list_sessions()

    service.mark_latest_terminal_run_viewed("session-terminal")
    sessions = service.list_sessions()

    record = sessions[0]
    assert record.latest_terminal_run_id == "run-terminal"
    assert record.last_viewed_terminal_run_id == "run-terminal"


@pytest.mark.asyncio
async def test_get_session_async_uses_list_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "session_get_async_cache.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active", workspace_id="default")
    _ = service.list_sessions()

    async def _fail_get_async(session_id: str) -> object:
        raise AssertionError(f"unexpected repository get_async for {session_id}")

    monkeypatch.setattr(service._session_repo, "get_async", _fail_get_async)

    record = await service.get_session_async("session-active")

    assert record.session_id == "session-active"


@pytest.mark.asyncio
async def test_create_session_async_merges_new_session_into_stale_list_cache(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_cache_create_merge.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-existing", workspace_id="default")
    _ = service.list_sessions()

    created = await service.create_session_async(
        session_id="session-created",
        workspace_id="default",
    )
    sessions = await service.list_sessions_async()

    assert created.session_id == "session-created"
    assert [record.session_id for record in sessions][:2] == [
        "session-created",
        "session-existing",
    ]


def test_list_sessions_by_workspace_filters_sessions(tmp_path: Path) -> None:
    db_path = tmp_path / "session_list_by_workspace.db"
    service = _build_service(db_path)
    _ = service.create_session(
        session_id="session-workspace-1",
        workspace_id="workspace-1",
    )
    _ = service.create_session(
        session_id="session-workspace-2",
        workspace_id="workspace-2",
    )

    sessions = service.list_sessions_by_workspace("workspace-1")

    assert [session.session_id for session in sessions] == ["session-workspace-1"]


def test_list_sessions_uses_runtime_overlay_for_running_subagent(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_subagent_status.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active", workspace_id="default")

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


def test_list_sessions_skips_invalid_persisted_run_runtime_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_status_invalid_runtime.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active", workspace_id="default")

    _seed_root_task(db_path, run_id="run-active", session_id="session-active")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-active",
        session_id="session-active",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-active",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _insert_invalid_run_runtime_row(
        db_path,
        run_id="run-invalid",
        session_id="session-active",
    )

    sessions = service.list_sessions()
    active = {record.session_id: record for record in sessions}["session-active"]

    assert active.has_active_run is True
    assert active.active_run_id == "run-active"
    assert active.active_run_phase == "running"


def test_list_sessions_skips_invalid_persisted_approval_ticket_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_list_status_invalid_approval.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-active", workspace_id="default")

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
    approval_repo = ApprovalTicketRepository(db_path)
    approval_repo.upsert_requested(
        tool_call_id="orch_dispatch_task:1",
        run_id="run-active",
        session_id="session-active",
        task_id="task-root-1",
        instance_id="inst-1",
        role_id="Coordinator",
        tool_name="orch_dispatch_task",
        args_preview='{"task_id":"task-1"}',
    )
    _insert_invalid_approval_ticket_row(
        db_path,
        tool_call_id="orch_dispatch_task:invalid",
        run_id="run-active",
        session_id="session-active",
    )

    sessions = service.list_sessions()
    active = {record.session_id: record for record in sessions}["session-active"]

    assert active.has_active_run is True
    assert active.pending_tool_approval_count == 1


def _insert_invalid_run_runtime_row(
    db_path: Path,
    *,
    run_id: str,
    session_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO run_runtime(
            run_id,
            session_id,
            root_task_id,
            status,
            phase,
            active_instance_id,
            active_task_id,
            active_role_id,
            active_subagent_instance_id,
            last_error,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            session_id,
            "task-bad",
            RunRuntimeStatus.RUNNING.value,
            RunRuntimePhase.COORDINATOR_RUNNING.value,
            None,
            None,
            None,
            None,
            None,
            now,
            "None",
        ),
    )
    connection.commit()
    connection.close()


def _insert_invalid_approval_ticket_row(
    db_path: Path,
    *,
    tool_call_id: str,
    run_id: str,
    session_id: str,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO approval_tickets(
            tool_call_id,
            signature_key,
            run_id,
            session_id,
            task_id,
            instance_id,
            role_id,
            tool_name,
            args_preview,
            status,
            feedback,
            created_at,
            updated_at,
            resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tool_call_id,
            "sig-invalid",
            run_id,
            session_id,
            "task-root-1",
            "inst-1",
            "Coordinator",
            "orch_dispatch_task",
            "{}",
            "requested",
            "",
            now,
            "None",
            None,
        ),
    )
    connection.commit()
    connection.close()


def test_list_normal_mode_subagents_reports_awaiting_tool_approval_phase(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_subagent_approval_phase.db"
    service = _build_service(db_path)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    _seed_root_task(db_path, run_id="run-root", session_id="session-1")
    runtime_repo = RunRuntimeRepository(db_path)
    runtime_repo.ensure(
        run_id="run-root",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "run-root",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )

    from relay_teams.agent_runtimes.instances.enums import InstanceStatus
    from relay_teams.agent_runtimes.instances.instance_repository import (
        AgentInstanceRepository,
    )

    AgentInstanceRepository(db_path).upsert_instance(
        run_id="subagent_run_proj123",
        trace_id="subagent_run_proj123",
        session_id="session-1",
        instance_id="inst-sub-1",
        role_id="Explorer",
        workspace_id="default",
        conversation_id="conv_session_1_explorer_inst_sub_1",
        status=InstanceStatus.RUNNING,
    )
    runtime_repo.ensure(
        run_id="subagent_run_proj123",
        session_id="session-1",
        root_task_id="task-root-1",
    )
    runtime_repo.update(
        "subagent_run_proj123",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    ApprovalTicketRepository(db_path).upsert_requested(
        tool_call_id="webfetch:1",
        run_id="subagent_run_proj123",
        session_id="session-1",
        task_id="task-root-1",
        instance_id="inst-sub-1",
        role_id="Explorer",
        tool_name="webfetch",
        args_preview='{"url":"https://example.com"}',
    )

    subagents = service.list_normal_mode_subagents("session-1")
    assert len(subagents) == 1
    assert subagents[0]["run_phase"] == "awaiting_tool_approval"
    assert subagents[0]["run_status"] == "paused"
