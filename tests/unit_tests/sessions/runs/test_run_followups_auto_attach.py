from __future__ import annotations

import pytest

from relay_teams.sessions.runs.run_followups import (
    assert_runtime_auto_attach_phase_allowed,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRecord,
    RunRuntimeStatus,
)


def _runtime(phase: RunRuntimePhase) -> RunRuntimeRecord:
    return RunRuntimeRecord(
        run_id="run-1",
        session_id="session-1",
        status=RunRuntimeStatus.PAUSED,
        phase=phase,
    )


def test_manual_action_phase_blocks_auto_attach() -> None:
    with pytest.raises(RuntimeError, match="manual gate"):
        assert_runtime_auto_attach_phase_allowed(
            "run-1",
            _runtime(RunRuntimePhase.AWAITING_MANUAL_ACTION),
        )


def test_idle_phase_allows_auto_attach() -> None:
    assert_runtime_auto_attach_phase_allowed(
        "run-1",
        _runtime(RunRuntimePhase.IDLE),
    )
