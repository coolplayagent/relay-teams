from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_teams.triggers.trigger_models import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerCreateInput,
    TriggerIngestInput,
    TriggerSourceType,
)
from agent_teams.triggers.trigger_repository import TriggerRepository
from agent_teams.triggers.trigger_service import (
    TriggerAuthRejectedError,
    TriggerService,
)


def _new_service(tmp_path: Path) -> TriggerService:
    return TriggerService(trigger_repo=TriggerRepository(tmp_path / "service.db"))


def test_service_creates_trigger_with_default_auth_policy(tmp_path: Path) -> None:
    service = _new_service(tmp_path)
    created = service.create_trigger(
        TriggerCreateInput(
            name="repo_push",
            source_type=TriggerSourceType.WEBHOOK,
        )
    )
    assert created.public_token is not None
    assert len(created.auth_policies) == 1
    assert created.auth_policies[0].mode == TriggerAuthMode.NONE


def test_ingest_webhook_rejects_when_header_token_missing(tmp_path: Path) -> None:
    service = _new_service(tmp_path)
    created = service.create_trigger(
        TriggerCreateInput(
            name="secure_webhook",
            source_type=TriggerSourceType.WEBHOOK,
            auth_policies=(
                TriggerAuthPolicy(
                    mode=TriggerAuthMode.HEADER_TOKEN,
                    header_name="X-Trigger-Token",
                    token="token-123",
                ),
            ),
        )
    )
    assert created.public_token is not None

    with pytest.raises(TriggerAuthRejectedError) as exc_info:
        _ = service.ingest_webhook(
            public_token=created.public_token,
            raw_body='{"payload":{"hello":"world"}}',
            headers={"content-type": "application/json"},
            remote_addr="127.0.0.1",
        )
    assert exc_info.value.event.status.value == "rejected_auth"


def test_ingest_event_returns_duplicate_for_same_event_key(tmp_path: Path) -> None:
    service = _new_service(tmp_path)
    created = service.create_trigger(
        TriggerCreateInput(
            name="dedupe_webhook",
            source_type=TriggerSourceType.WEBHOOK,
        )
    )
    first = service.ingest_event(
        TriggerIngestInput(
            trigger_id=created.trigger_id,
            source_type=TriggerSourceType.WEBHOOK,
            event_key="evt-1",
            occurred_at=datetime.now(tz=UTC),
            payload={"hello": "world"},
        ),
        headers={"content-type": "application/json"},
        remote_addr="127.0.0.1",
        raw_body='{"hello":"world"}',
    )
    second = service.ingest_event(
        TriggerIngestInput(
            trigger_id=created.trigger_id,
            source_type=TriggerSourceType.WEBHOOK,
            event_key="evt-1",
            occurred_at=datetime.now(tz=UTC),
            payload={"hello": "world"},
        ),
        headers={"content-type": "application/json"},
        remote_addr="127.0.0.1",
        raw_body='{"hello":"world"}',
    )

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.event_id == first.event_id
