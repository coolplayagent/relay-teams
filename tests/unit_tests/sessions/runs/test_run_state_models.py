from __future__ import annotations

from datetime import UTC, datetime

from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.run_state_models import (
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


def test_run_paused_transitions_run_to_awaiting_recovery() -> None:
    previous = RunStateRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunStateStatus.RUNNING,
        phase=RunStatePhase.STREAMING,
        recoverable=True,
        last_event_id=4,
        checkpoint_event_id=4,
        pending_tool_approvals=(),
        paused_subagent=None,
        updated_at=datetime(2026, 3, 6, 0, 0, tzinfo=UTC),
    )

    paused = apply_run_event_to_state(
        previous,
        event=_build_event(
            RunEventType.RUN_PAUSED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
        ),
        event_id=5,
    )

    assert paused.status == RunStateStatus.PAUSED
    assert paused.phase == RunStatePhase.AWAITING_RECOVERY
    assert paused.recoverable is True
    assert paused.last_event_id == 5
    assert paused.checkpoint_event_id == 5


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


def test_user_question_events_project_run_to_awaiting_manual_action() -> None:
    requested = apply_run_event_to_state(
        None,
        event=_build_event(
            RunEventType.USER_QUESTION_REQUESTED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
            payload_json=(
                '{"question_id":"call-1","questions":[{"question":"Pick one",'
                '"options":[{"label":"A","description":"Option A"}]}]}'
            ),
        ),
        event_id=1,
    )

    assert requested.status == RunStateStatus.PAUSED
    assert requested.phase == RunStatePhase.AWAITING_MANUAL_ACTION
    assert len(requested.pending_user_questions) == 1
    assert requested.pending_user_questions[0].question_id == "call-1"

    answered = apply_run_event_to_state(
        requested,
        event=_build_event(
            RunEventType.USER_QUESTION_ANSWERED,
            occurred_at=datetime(2026, 3, 6, 0, 2, tzinfo=UTC),
            payload_json='{"question_id":"call-1"}',
        ),
        event_id=2,
    )

    assert answered.status == RunStateStatus.RUNNING
    assert answered.phase == RunStatePhase.STREAMING
    assert answered.pending_user_questions == ()


def test_completed_user_question_keeps_subagent_followup_phase() -> None:
    requested = apply_run_event_to_state(
        None,
        event=_build_event(
            RunEventType.USER_QUESTION_REQUESTED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
            payload_json=(
                '{"question_id":"call-1","questions":[{"question":"Pick one",'
                '"options":[{"label":"A","description":"Option A"}]}]}'
            ),
        ),
        event_id=1,
    )

    paused = apply_run_event_to_state(
        requested,
        event=_build_event(
            RunEventType.SUBAGENT_STOPPED,
            occurred_at=datetime(2026, 3, 6, 0, 2, tzinfo=UTC),
            payload_json='{"instance_id":"inst-2","role_id":"Writer"}',
        ),
        event_id=2,
    )

    completed = apply_run_event_to_state(
        paused,
        event=_build_event(
            RunEventType.USER_QUESTION_ANSWERED,
            occurred_at=datetime(2026, 3, 6, 0, 3, tzinfo=UTC),
            payload_json='{"question_id":"call-1","status":"completed"}',
        ),
        event_id=3,
    )

    assert completed.status == RunStateStatus.PAUSED
    assert completed.phase == RunStatePhase.AWAITING_SUBAGENT_FOLLOWUP
    assert completed.pending_user_questions == ()
    assert completed.paused_subagent is not None
    assert completed.paused_subagent.instance_id == "inst-2"


def test_run_stopped_clears_pending_user_questions() -> None:
    requested = apply_run_event_to_state(
        None,
        event=_build_event(
            RunEventType.USER_QUESTION_REQUESTED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
            payload_json=(
                '{"question_id":"call-1","questions":[{"question":"Pick one",'
                '"options":[{"label":"A","description":"Option A"}]}]}'
            ),
        ),
        event_id=1,
    )

    stopped = apply_run_event_to_state(
        requested,
        event=_build_event(
            RunEventType.RUN_STOPPED,
            occurred_at=datetime(2026, 3, 6, 0, 2, tzinfo=UTC),
            payload_json='{"reason":"stopped_by_user"}',
        ),
        event_id=2,
    )

    assert stopped.status == RunStateStatus.STOPPED
    assert stopped.phase == RunStatePhase.TERMINAL
    assert stopped.pending_user_questions == ()


def test_run_resumed_after_stop_does_not_restore_stale_pending_user_questions() -> None:
    requested = apply_run_event_to_state(
        None,
        event=_build_event(
            RunEventType.USER_QUESTION_REQUESTED,
            occurred_at=datetime(2026, 3, 6, 0, 1, tzinfo=UTC),
            payload_json=(
                '{"question_id":"call-1","questions":[{"question":"Pick one",'
                '"options":[{"label":"A","description":"Option A"}]}]}'
            ),
        ),
        event_id=1,
    )
    stopped = apply_run_event_to_state(
        requested,
        event=_build_event(
            RunEventType.RUN_STOPPED,
            occurred_at=datetime(2026, 3, 6, 0, 2, tzinfo=UTC),
            payload_json='{"reason":"stopped_by_user"}',
        ),
        event_id=2,
    )

    resumed = apply_run_event_to_state(
        stopped,
        event=_build_event(
            RunEventType.RUN_RESUMED,
            occurred_at=datetime(2026, 3, 6, 0, 3, tzinfo=UTC),
        ),
        event_id=3,
    )

    assert resumed.status == RunStateStatus.RUNNING
    assert resumed.phase == RunStatePhase.STREAMING
    assert resumed.pending_user_questions == ()
