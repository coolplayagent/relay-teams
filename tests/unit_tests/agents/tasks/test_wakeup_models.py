# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupReason, WakeupStatus
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry


def _make_entry(
    *,
    wakeup_id: str = "wk_001",
    task_id: str = "task_001",
    trace_id: str = "trace_001",
    session_id: str = "sess_001",
    coalesce_key: str = "task_001:retry",
    timeout_action: TaskTimeoutAction = TaskTimeoutAction.RETRY,
    timeout_seconds: float = 60.0,
    attempt: int = 1,
    max_attempts: int = 3,
    status: WakeupStatus = WakeupStatus.PENDING,
    enqueued_at: datetime | None = None,
    wake_reason: WakeupReason = WakeupReason.TIMEOUT_RETRY,
    target_role: str = "",
    target_instance: str = "",
    source_event_type: str = "",
    source_trigger_id: str = "",
    claimed_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> AgentWakeupEntry:
    return AgentWakeupEntry(
        wakeup_id=wakeup_id,
        task_id=task_id,
        trace_id=trace_id,
        session_id=session_id,
        coalesce_key=coalesce_key,
        timeout_action=timeout_action,
        timeout_seconds=timeout_seconds,
        attempt=attempt,
        max_attempts=max_attempts,
        status=status,
        enqueued_at=enqueued_at or datetime.now(tz=timezone.utc),
        wake_reason=wake_reason,
        target_role=target_role,
        target_instance=target_instance,
        source_event_type=source_event_type,
        source_trigger_id=source_trigger_id,
        claimed_at=claimed_at,
        completed_at=completed_at,
    )


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
            AgentWakeupEntry(
                wakeup_id="wk_001",
                task_id="task_001",
                trace_id="trace_001",
                session_id="sess_001",
                coalesce_key="task_001:retry",
                timeout_action=TaskTimeoutAction.RETRY,
                timeout_seconds=60.0,
                attempt=1,
                max_attempts=3,
                status=WakeupStatus.PENDING,
                enqueued_at=datetime.now(tz=timezone.utc),
                unknown="value",  # type: ignore[arg-type]
            )

    def test_all_statuses(self) -> None:
        for status in WakeupStatus.__members__.values():
            entry = _make_entry(status=status)
            assert entry.status == status

    def test_all_timeout_actions(self) -> None:
        for action in TaskTimeoutAction.__members__.values():
            entry = _make_entry(timeout_action=action)
            assert entry.timeout_action == action
