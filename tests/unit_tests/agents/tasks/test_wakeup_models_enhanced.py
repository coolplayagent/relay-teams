# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from relay_teams.agents.tasks.enums import (
    TaskTimeoutAction,
    WakeupReason,
    WakeupStatus,
)
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


def _make_entry(**overrides: object) -> AgentWakeupEntry:
    defaults: dict[str, object] = dict(
        wakeup_id="wk_test_1",
        task_id="task_1",
        trace_id="trace_1",
        session_id="sess_1",
        coalesce_key="task_1:retry",
        timeout_action=TaskTimeoutAction.RETRY,
        timeout_seconds=60.0,
        attempt=1,
        max_attempts=3,
        status=WakeupStatus.PENDING,
        enqueued_at=datetime.now(tz=timezone.utc),
    )
    defaults.update(overrides)
    return AgentWakeupEntry(**defaults)  # type: ignore[arg-type]


class TestWakeupModels:
    def test_default_wake_reason(self) -> None:
        entry = _make_entry()
        assert entry.wake_reason == WakeupReason.TIMEOUT_RETRY

    @pytest.mark.parametrize(
        "reason",
        list(WakeupReason),
    )
    def test_all_wakeup_reasons(self, reason: WakeupReason) -> None:
        entry = _make_entry(wake_reason=reason)
        assert entry.wake_reason == reason

    def test_target_fields_default_empty(self) -> None:
        entry = _make_entry()
        assert entry.target_role == ""
        assert entry.target_instance == ""

    def test_target_fields_set(self) -> None:
        entry = _make_entry(target_role="Crafter", target_instance="inst_1")
        assert entry.target_role == "Crafter"
        assert entry.target_instance == "inst_1"

    def test_frozen_model(self) -> None:
        entry = _make_entry()
        with pytest.raises(Exception):
            entry.wake_reason = WakeupReason.ORPHAN_RECOVERY  # type: ignore[misc]
