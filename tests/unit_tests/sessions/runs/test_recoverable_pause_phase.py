from __future__ import annotations

from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPausePayload
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase
from relay_teams.sessions.runs.run_service import _recoverable_pause_phase


def _payload(
    *, runtime_phase: RunRuntimePhase | None = None
) -> RecoverableRunPausePayload:
    return RecoverableRunPausePayload(
        run_id="run-1",
        trace_id="run-1",
        task_id="task-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="Coordinator",
        error_message="paused",
        runtime_phase=runtime_phase,
    )


def test_recoverable_pause_phase_defaults_to_recovery() -> None:
    assert _recoverable_pause_phase(_payload()) == RunRuntimePhase.AWAITING_RECOVERY


def test_recoverable_pause_phase_preserves_payload_phase() -> None:
    assert (
        _recoverable_pause_phase(
            _payload(runtime_phase=RunRuntimePhase.AWAITING_MANUAL_ACTION)
        )
        == RunRuntimePhase.AWAITING_MANUAL_ACTION
    )
