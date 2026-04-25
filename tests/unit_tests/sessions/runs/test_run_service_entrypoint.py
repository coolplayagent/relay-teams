# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.sessions.runs.run_recovery import AutoRecoveryReason
from relay_teams.sessions.runs.run_service import SessionRunService


def test_session_run_service_is_available_from_explicit_module() -> None:
    assert SessionRunService.__name__ == "SessionRunService"


def test_auto_recovery_types_remain_available_from_recovery_module() -> None:
    assert AutoRecoveryReason.NETWORK_TIMEOUT.value == "auto_recovery_network_timeout"
