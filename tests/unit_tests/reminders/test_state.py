from __future__ import annotations

from datetime import datetime, timedelta, timezone
from json import dumps
from pathlib import Path

from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.reminders.state import (
    ReminderRunState,
    ReminderStateRepository,
    can_issue,
    mark_issued,
)


def test_state_repository_persists_run_state(tmp_path: Path) -> None:
    repository = ReminderStateRepository(SharedStateRepository(tmp_path / "state.db"))
    state = ReminderRunState(read_only_streak=3, completion_retry_count=1)

    repository.save_run_state(session_id="session-1", run_id="run-1", state=state)

    restored = repository.get_run_state(session_id="session-1", run_id="run-1")
    assert restored == state


def test_state_repository_ignores_invalid_persisted_state(tmp_path: Path) -> None:
    shared = SharedStateRepository(tmp_path / "state.db")
    shared.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.SESSION, scope_id="session-1"),
            key="system_reminders:run-1",
            value_json=dumps({"read_only_streak": -1}),
        )
    )
    repository = ReminderStateRepository(shared)

    restored = repository.get_run_state(session_id="session-1", run_id="run-1")

    assert restored == ReminderRunState()


def test_can_issue_respects_cooldown() -> None:
    now = datetime.now(tz=timezone.utc)
    state = mark_issued(
        state=ReminderRunState(),
        issue_key="tool_failure:read:file_missing",
        now=now,
    )

    assert (
        can_issue(
            state=state,
            issue_key="tool_failure:read:file_missing",
            cooldown_seconds=60,
            now=now + timedelta(seconds=30),
        )
        is False
    )
    assert (
        can_issue(
            state=state,
            issue_key="tool_failure:read:file_missing",
            cooldown_seconds=60,
            now=now + timedelta(seconds=61),
        )
        is True
    )
