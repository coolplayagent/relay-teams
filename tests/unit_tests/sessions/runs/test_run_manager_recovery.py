# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import pytest

from agent_teams.agents.orchestration.meta_agent import MetaAgent
from agent_teams.agents.enums import InstanceStatus
from agent_teams.sessions.runs.active_registry import ActiveSessionRunRegistry
from agent_teams.sessions.runs.control import RunControlManager
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.event_stream import RunEventHub
from agent_teams.sessions.runs.injection_queue import RunInjectionManager
from agent_teams.sessions.runs.manager import RunManager
from agent_teams.sessions.runs.models import IntentInput, RunResult
from agent_teams.agents.agent_repo import AgentInstanceRepository
from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository
from agent_teams.sessions.runs.event_log import EventLog
from agent_teams.agents.execution.message_repo import MessageRepository
from agent_teams.sessions.runs.run_intent_repo import RunIntentRepository
from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from agent_teams.sessions.runs.run_state_repo import RunStateRepository
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.sessions.session_repo import SessionRepository
from agent_teams.agents.tasks.task_repo import TaskRepository
from agent_teams.tools.runtime import ToolApprovalManager
from agent_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from agent_teams.workspace import build_workspace_id


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
            workspace_id=build_workspace_id(session_id),
        )

    def create(
        self,
        session_id: str,
        metadata: dict[str, str] | None = None,
    ) -> SessionRecord:
        return SessionRecord(
            session_id=session_id,
            workspace_id=build_workspace_id(session_id),
            metadata=metadata or {},
        )


class _EventBus:
    def emit(self, event) -> None:
        _ = event


def _build_manager(db_path: Path) -> RunManager:
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
        event_log=event_log,
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
        role_id="coordinator_agent",
        status=InstanceStatus.RUNNING,
    )


def _create_root_task(task_repo: TaskRepository) -> None:
    _ = task_repo.create(
        TaskEnvelope(
            task_id="task-root-1",
            session_id="session-1",
            parent_task_id=None,
            trace_id="run-existing",
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
        IntentInput(session_id="session-1", intent="follow up")
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
        IntentInput(session_id="session-1", intent="continue from checkpoint")
    )

    assert run_id == "run-existing"
    assert session_id == "session-1"
    assert "run-existing" in manager._resume_requested_runs


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
        role_id="coordinator_agent",
        tool_name="create_tasks",
        args_preview="{}",
    )
    manager._active_run_registry.remember_active_run(
        session_id="session-1",
        run_id="run-existing",
    )

    with pytest.raises(RuntimeError, match="waiting for tool approval"):
        manager.create_run(IntentInput(session_id="session-1", intent="continue"))


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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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
        role_id="coordinator_agent",
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result_status", "runtime_status", "terminal_event_type"),
    [
        ("completed", RunRuntimeStatus.COMPLETED, RunEventType.RUN_COMPLETED),
        ("failed", RunRuntimeStatus.FAILED, RunEventType.RUN_FAILED),
    ],
)
async def test_worker_terminal_status_matches_run_result(
    tmp_path: Path,
    result_status: str,
    runtime_status: RunRuntimeStatus,
    terminal_event_type: RunEventType,
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
            output="Task not completed yet" if result_status == "failed" else "done",
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
    if result_status == "failed":
        assert runtime.last_error == "Task not completed yet"
    else:
        assert runtime.last_error is None

    state = run_state_repo.get_run_state("run-existing")
    assert state is not None
    assert state.status.value == result_status
    assert state.phase.value == "terminal"
    assert state.recoverable is False

    events = event_log.list_by_session_with_ids("session-1")
    assert events[-1]["event_type"] == terminal_event_type.value
