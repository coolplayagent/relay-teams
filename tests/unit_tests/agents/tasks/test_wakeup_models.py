# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


def _make_entry(**overrides: object) -> AgentWakeupEntry:
    defaults = {
        "wakeup_id": "wk_001",
        "task_id": "task_001",
        "trace_id": "trace_001",
        "session_id": "sess_001",
        "coalesce_key": "task_001:retry",
        "timeout_action": TaskTimeoutAction.RETRY,
        "timeout_seconds": 60.0,
        "attempt": 1,
        "max_attempts": 3,
        "status": WakeupStatus.PENDING,
        "enqueued_at": datetime.now(tz=timezone.utc),
    }
    defaults.update(overrides)
    return AgentWakeupEntry(**defaults)  # type: ignore[arg-type]


class TestAgentWakeupEntry:
    def test_construction(self) -> None:
        entry = _make_entry()
        assert entry.wakeup_id == "wk_001"
        assert entry.status == WakeupStatus.PENDING
        assert entry.attempt == 1

    def test_frozen(self) -> None:
        entry = _make_entry()
        with pytest.raises(ValidationError):
            entry.status = WakeupStatus.CLAIMED  # type: ignore[misc]

    def test_optional_fields_default_none(self) -> None:
        entry = _make_entry()
        assert entry.claimed_at is None
        assert entry.completed_at is None

    def test_optional_fields_set(self) -> None:
        now = datetime.now(tz=timezone.utc)
        entry = _make_entry(claimed_at=now, completed_at=now)
        assert entry.claimed_at == now
        assert entry.completed_at == now

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            _make_entry(unknown="value")  # type: ignore[arg-type]

    def test_all_statuses(self) -> None:
        for status in WakeupStatus:
            entry = _make_entry(status=status)
            assert entry.status == status

    def test_all_timeout_actions(self) -> None:
        for action in TaskTimeoutAction:
            entry = _make_entry(timeout_action=action)
            assert entry.timeout_action == action
