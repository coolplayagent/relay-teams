from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_teams.triggers.trigger_models import (
    TriggerAuthMode,
    TriggerAuthPolicy,
    TriggerIngestInput,
    TriggerSourceType,
)


def test_header_token_policy_requires_header_name_and_token() -> None:
    with pytest.raises(ValidationError):
        _ = TriggerAuthPolicy(mode=TriggerAuthMode.HEADER_TOKEN)

    policy = TriggerAuthPolicy(
        mode=TriggerAuthMode.HEADER_TOKEN,
        header_name="X-Trigger-Token",
        token="secret-token",
    )
    assert policy.mode == TriggerAuthMode.HEADER_TOKEN


def test_hmac_policy_requires_secret() -> None:
    with pytest.raises(ValidationError):
        _ = TriggerAuthPolicy(mode=TriggerAuthMode.HMAC_SHA256)

    policy = TriggerAuthPolicy(
        mode=TriggerAuthMode.HMAC_SHA256,
        secret="s3cr3t",
    )
    assert policy.mode == TriggerAuthMode.HMAC_SHA256


def test_ingest_selector_requires_trigger_id_or_name() -> None:
    with pytest.raises(ValidationError):
        _ = TriggerIngestInput(
            source_type=TriggerSourceType.WEBHOOK,
            payload={"hello": "world"},
        )

    req = TriggerIngestInput(
        trigger_name="repo_push",
        source_type=TriggerSourceType.WEBHOOK,
        payload={"hello": "world"},
    )
    assert req.trigger_name == "repo_push"
