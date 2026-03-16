from __future__ import annotations

from datetime import UTC, datetime

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.sessions.runs.run_state_models import (
    RunStatePhase,
    RunStateRecord,
    RunStateStatus,
    apply_run_event_to_state,
)


def _build_event(
    event_type: RunEventType,
    *,
    occurred_at: datetime,
    payload_json: str = "{}",
) -> RunEvent:
    return RunEvent(
        session_id="session-1",
        run_id="run-1",
        trace_id="run-1",
        task_id="task-root-1",
        event_type=event_type,
        payload_json=payload_json,
        occurred_at=occurred_at,
    )


def test_run_resumed_transitions_stopped_run_back_to_running() -> None:
    previous = RunStateRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunStateStatus.STOPPED,
        phase=RunStatePhase.TERMINAL,
        recoverable=True,
        last_event_id=3,
        checkpoint_event_id=3,
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=datetime(2026, 3, 6, 0, 0, tzinfo=UTC),
    )

    resumed = apply_run_event_to_state(
        previous,
        event=_build_event(
            RunEventType.RUN_RESUMED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
        ),
        event_id=4,
    )

    assert resumed.status == RunStateStatus.RUNNING
    assert resumed.phase == RunStatePhase.STREAMING
    assert resumed.recoverable is True
    assert resumed.last_event_id == 4
    assert resumed.checkpoint_event_id == 4


def test_text_delta_does_not_advance_checkpoint_event_id() -> None:
    previous = RunStateRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunStateStatus.RUNNING,
        phase=RunStatePhase.STREAMING,
        recoverable=True,
        last_event_id=5,
        checkpoint_event_id=5,
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=datetime(2026, 3, 6, 0, 0, tzinfo=UTC),
    )

    streamed = apply_run_event_to_state(
        previous,
        event=_build_event(
            RunEventType.TEXT_DELTA,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
        ),
        event_id=6,
    )

    assert streamed.status == RunStateStatus.RUNNING
    assert streamed.phase == RunStatePhase.STREAMING
    assert streamed.last_event_id == 6
    assert streamed.checkpoint_event_id == 5


def test_completed_run_ignores_late_events() -> None:
    previous = RunStateRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunStateStatus.COMPLETED,
        phase=RunStatePhase.TERMINAL,
        recoverable=False,
        last_event_id=9,
        checkpoint_event_id=9,
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=datetime(2026, 3, 6, 0, 0, tzinfo=UTC),
    )

    ignored = apply_run_event_to_state(
        previous,
        event=_build_event(
            RunEventType.TOOL_RESULT,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
        ),
        event_id=10,
    )

    assert ignored == previous


def test_run_completed_event_with_failed_payload_is_projected_as_failed() -> None:
    previous = RunStateRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunStateStatus.RUNNING,
        phase=RunStatePhase.STREAMING,
        recoverable=True,
        last_event_id=11,
        checkpoint_event_id=11,
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=datetime(2026, 3, 6, 0, 0, tzinfo=UTC),
    )

    projected = apply_run_event_to_state(
        previous,
        event=_build_event(
            RunEventType.RUN_COMPLETED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
            payload_json='{"status":"failed","output":"Task not completed yet"}',
        ),
        event_id=12,
    )

    assert projected.status == RunStateStatus.FAILED
    assert projected.phase == RunStatePhase.TERMINAL
    assert projected.recoverable is False
