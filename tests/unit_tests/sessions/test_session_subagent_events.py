from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.agents.instances.instance_repository import AgentInstanceRepository
from relay_teams.agents.tasks.task_repository import TaskRepository
from relay_teams.providers.token_usage_repo import TokenUsageRepository
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_log import EventLog
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_state_repo import RunStateRepository
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimeRepository
from relay_teams.sessions.session_models import SessionMode
from relay_teams.sessions.session_repository import SessionRepository
from relay_teams.sessions.session_service import SessionService
from relay_teams.tools.runtime.approval_ticket_repo import ApprovalTicketRepository


def _build_service(
    db_path: Path,
    event_log: EventLog,
    *,
    run_event_hub: RunEventHub | None = None,
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
        event_log=event_log,
    )


@pytest.mark.asyncio
async def test_stream_normal_mode_subagent_events_replays_only_subagent_runs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_subagent_events.db"
    event_log = EventLog(db_path)
    service = _build_service(db_path, event_log)
    _ = service.create_session(session_id="session-1", workspace_id="default")
    _ = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="run-main",
            trace_id="run-main",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        )
    )
    subagent_event_id = event_log.emit_run_event(
        RunEvent(
            session_id="session-1",
            run_id="subagent_run_1",
            trace_id="subagent_run_1",
            instance_id="instance-sub-1",
            role_id="Explorer",
            event_type=RunEventType.MODEL_STEP_STARTED,
            payload_json='{"instance_id":"instance-sub-1","role_id":"Explorer"}',
        )
    )

    events = [
        event
        async for event in service.stream_normal_mode_subagent_events(
            "session-1",
            after_event_id=0,
        )
    ]

    assert [event.run_id for event in events] == ["subagent_run_1"]
    assert events[0].event_id == subagent_event_id
    assert events[0].instance_id == "instance-sub-1"


@pytest.mark.asyncio
async def test_stream_normal_mode_subagent_events_delivers_live_session_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_subagent_events_live.db"
    event_log = EventLog(db_path)
    run_event_hub = RunEventHub(
        event_log=event_log,
        run_state_repo=RunStateRepository(db_path),
    )
    service = _build_service(db_path, event_log, run_event_hub=run_event_hub)
    _ = service.create_session(session_id="session-1", workspace_id="default")

    async def read_first_live_subagent_event() -> RunEvent:
        async for event in service.stream_normal_mode_subagent_events(
            "session-1",
            after_event_id=0,
        ):
            return event
        raise AssertionError("expected a live subagent event")

    task = asyncio.create_task(read_first_live_subagent_event())
    for _ in range(20):
        if run_event_hub.has_session_subscribers("session-1"):
            break
        await asyncio.sleep(0)
    assert run_event_hub.has_session_subscribers("session-1") is True

    await run_event_hub.publish_async(
        RunEvent(
            session_id="session-1",
            run_id="run-main",
            trace_id="run-main",
            event_type=RunEventType.RUN_STARTED,
            payload_json="{}",
        )
    )
    await run_event_hub.publish_async(
        RunEvent(
            session_id="session-1",
            run_id="subagent_run_live",
            trace_id="subagent_run_live",
            event_type=RunEventType.MODEL_STEP_STARTED,
            payload_json="{}",
        )
    )

    delivered = await asyncio.wait_for(task, timeout=1.0)

    assert delivered.run_id == "subagent_run_live"
    assert delivered.event_id == 2
    for _ in range(20):
        if not run_event_hub.has_session_subscribers("session-1"):
            break
        await asyncio.sleep(0)
    assert run_event_hub.has_session_subscribers("session-1") is False


@pytest.mark.asyncio
async def test_stream_normal_mode_subagent_events_ignores_orchestration_sessions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "session_subagent_events_orchestration.db"
    event_log = EventLog(db_path)
    service = _build_service(db_path, event_log)
    _ = service.create_session(
        session_id="session-1",
        workspace_id="default",
        session_mode=SessionMode.ORCHESTRATION,
    )

    events = [
        event
        async for event in service.stream_normal_mode_subagent_events(
            "session-1",
            after_event_id=0,
        )
    ]

    assert events == []


def test_run_event_from_log_row_rejects_invalid_persisted_rows() -> None:
    assert SessionService._run_event_from_log_row({"id": "1"}) is None
    assert (
        SessionService._run_event_from_log_row(
            {"id": 1, "event_type": "not-valid", "trace_id": "run-1", "session_id": "s"}
        )
        is None
    )
    assert (
        SessionService._run_event_from_log_row(
            {"id": 1, "event_type": "run_started", "trace_id": "", "session_id": "s"}
        )
        is None
    )
    assert (
        SessionService._run_event_from_log_row(
            {
                "id": 1,
                "event_type": "run_started",
                "trace_id": "run-1",
                "session_id": "",
            }
        )
        is None
    )

    event = SessionService._run_event_from_log_row(
        {
            "id": 2,
            "event_type": "run_started",
            "trace_id": "subagent_run_1",
            "session_id": "session-1",
            "task_id": "task-1",
            "instance_id": "inst-1",
            "payload_json": '{"ok":true}',
        }
    )

    assert event is not None
    assert event.event_id == 2
    assert event.run_id == "subagent_run_1"
    assert SessionService._is_subagent_run_id("subagent_run_1") is True
    assert SessionService._is_subagent_run_id("run-main") is False
