from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_teams.triggers.trigger_models import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerDefinition,
    TriggerEventRecord,
    TriggerEventStatus,
    TriggerSourceType,
    TriggerStatus,
)
from agent_teams.triggers.trigger_repository import (
    TriggerEventDuplicateError,
    TriggerRepository,
)


def _build_trigger(name: str = "repo_push") -> TriggerDefinition:
    now = datetime.now(tz=UTC)
    return TriggerDefinition(
        trigger_id="trg_1",
        name=name,
        display_name=name,
        source_type=TriggerSourceType.WEBHOOK,
        status=TriggerStatus.ENABLED,
        public_token="token-1",
        source_config={},
        auth_policies=(TriggerAuthPolicy(mode=TriggerAuthMode.NONE),),
        target_config=None,
        created_at=now,
        updated_at=now,
    )


def _build_event(event_key: str | None = None) -> TriggerEventRecord:
    return TriggerEventRecord(
        sequence_id=0,
        event_id="tev_1",
        trigger_id="trg_1",
        trigger_name="repo_push",
        source_type=TriggerSourceType.WEBHOOK,
        event_key=event_key,
        status=TriggerEventStatus.RECEIVED,
        received_at=datetime.now(tz=UTC),
        occurred_at=None,
        payload={"event": "push"},
        metadata={},
        headers={"content-type": "application/json"},
        remote_addr="127.0.0.1",
        auth_mode=TriggerAuthMode.NONE,
        auth_result="accepted",
        auth_reason="no_auth_required",
    )


def test_repository_persists_trigger_and_event(tmp_path: Path) -> None:
    repo = TriggerRepository(tmp_path / "triggers.db")
    created = repo.create_trigger(_build_trigger())
    fetched = repo.get_trigger(created.trigger_id)
    assert fetched.name == "repo_push"

    stored_event = repo.create_event(_build_event(event_key="evt-1"))
    assert stored_event.sequence_id > 0
    fetched_event = repo.get_event(stored_event.event_id)
    assert fetched_event.event_key == "evt-1"


def test_repository_raises_on_duplicate_event_key(tmp_path: Path) -> None:
    repo = TriggerRepository(tmp_path / "triggers.db")
    _ = repo.create_trigger(_build_trigger())
    _ = repo.create_event(_build_event(event_key="evt-1"))

    with pytest.raises(TriggerEventDuplicateError):
        _ = repo.create_event(
            _build_event(event_key="evt-1").model_copy(update={"event_id": "tev_2"})
        )
