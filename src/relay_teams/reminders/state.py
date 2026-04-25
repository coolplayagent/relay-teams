from __future__ import annotations

import logging
from datetime import datetime, timezone
from json import dumps
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository

LOGGER = get_logger(__name__)
STATE_KEY_PREFIX = "system_reminders"


class ReminderRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_only_streak: int = Field(default=0, ge=0)
    completion_retry_count: int = Field(default=0, ge=0)
    issued_at_by_key: dict[str, str] = Field(default_factory=dict)


class ReminderStateRepository:
    def __init__(self, repository: SharedStateRepository) -> None:
        self._repository = repository

    def get_run_state(self, *, session_id: str, run_id: str) -> ReminderRunState:
        raw = self._repository.get_state(_scope(session_id), _state_key(run_id))
        return _parse_run_state(raw, session_id=session_id, run_id=run_id)

    async def get_run_state_async(
        self, *, session_id: str, run_id: str
    ) -> ReminderRunState:
        raw = await self._repository.get_state_async(
            _scope(session_id), _state_key(run_id)
        )
        return _parse_run_state(raw, session_id=session_id, run_id=run_id)

    def save_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        state: ReminderRunState,
    ) -> None:
        self._repository.manage_state(
            _run_state_mutation(session_id=session_id, run_id=run_id, state=state)
        )

    async def save_run_state_async(
        self,
        *,
        session_id: str,
        run_id: str,
        state: ReminderRunState,
    ) -> None:
        await self._repository.manage_state_async(
            _run_state_mutation(session_id=session_id, run_id=run_id, state=state)
        )


def _parse_run_state(
    raw: str | None,
    *,
    session_id: str,
    run_id: str,
) -> ReminderRunState:
    if raw is None:
        return ReminderRunState()
    try:
        return ReminderRunState.model_validate_json(raw)
    except ValidationError as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="reminders.state.invalid",
            message="Ignoring invalid persisted reminder state",
            payload={"session_id": session_id, "run_id": run_id},
            exc_info=exc,
        )
        return ReminderRunState()


def _run_state_mutation(
    *,
    session_id: str,
    run_id: str,
    state: ReminderRunState,
) -> StateMutation:
    return StateMutation(
        scope=_scope(session_id),
        key=_state_key(run_id),
        value_json=dumps(state.model_dump(mode="json"), ensure_ascii=False),
    )


def can_issue(
    *,
    state: ReminderRunState,
    issue_key: str,
    cooldown_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    current_time = now or datetime.now(tz=timezone.utc)
    raw = state.issued_at_by_key.get(issue_key)
    if not raw:
        return True
    try:
        previous = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    return (current_time - previous).total_seconds() >= cooldown_seconds


def mark_issued(
    *,
    state: ReminderRunState,
    issue_key: str,
    now: Optional[datetime] = None,
) -> ReminderRunState:
    current_time = now or datetime.now(tz=timezone.utc)
    issued_at_by_key = dict(state.issued_at_by_key)
    issued_at_by_key[issue_key] = current_time.isoformat()
    return state.model_copy(update={"issued_at_by_key": issued_at_by_key})


def _scope(session_id: str) -> ScopeRef:
    return ScopeRef(scope_type=ScopeType.SESSION, scope_id=session_id)


def _state_key(run_id: str) -> str:
    return f"{STATE_KEY_PREFIX}:{run_id}"
