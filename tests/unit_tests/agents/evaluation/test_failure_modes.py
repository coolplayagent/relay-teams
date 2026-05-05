# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)


def test_failure_mode_enum_values() -> None:
    """Verify all five enum members exist with correct string values."""
    modes = list(FailureMode)
    assert len(modes) == 5
    assert FailureMode.CONTEXT_ROT.value == "context_rot"
    assert FailureMode.TOOL_SPRAWL.value == "tool_sprawl"
    assert FailureMode.SPEC_DRIFT.value == "spec_drift"
    assert FailureMode.PERMISSION_FRICTION.value == "permission_friction"
    assert FailureMode.VERIFICATION_MISS.value == "verification_miss"


def test_failure_mode_classification_creation() -> None:
    """Instantiate FailureModeClassification with valid data."""
    now = datetime.now(timezone.utc)
    classification = FailureModeClassification(
        classification_id="fmc-test123456",
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
        role_id="role-1",
        primary_mode=FailureMode.CONTEXT_ROT,
        secondary_modes=(FailureMode.TOOL_SPRAWL,),
        confidence_score=0.85,
        evidence_summary="Test evidence",
        evidence_refs=("evt-1", "evt-2"),
        classified_at=now,
        classifier_version="1.0.0",
    )
    assert classification.classification_id == "fmc-test123456"
    assert classification.run_id == "run-1"
    assert classification.session_id == "sess-1"
    assert classification.workspace_id == "ws-1"
    assert classification.role_id == "role-1"
    assert classification.primary_mode == FailureMode.CONTEXT_ROT
    assert classification.secondary_modes == (FailureMode.TOOL_SPRAWL,)
    assert classification.confidence_score == 0.85
    assert classification.evidence_summary == "Test evidence"
    assert classification.evidence_refs == ("evt-1", "evt-2")
    assert classification.classified_at == now
    assert classification.classifier_version == "1.0.0"


def test_failure_mode_classification_validation() -> None:
    """Missing required fields raise ValidationError."""
    with pytest.raises(ValidationError):
        FailureModeClassification.model_validate(
            {"classification_id": "fmc-test", "primary_mode": "context_rot"},
        )


def test_failure_mode_classification_confidence_bounds() -> None:
    """Verify confidence_score 0.0 and 1.0 are valid; -0.1 and 1.1 are rejected."""

    def _make_classification(score: float) -> FailureModeClassification:
        return FailureModeClassification(
            classification_id="fmc-test",
            run_id="run-1",
            session_id="sess-1",
            workspace_id="ws-1",
            primary_mode=FailureMode.CONTEXT_ROT,
            confidence_score=score,
            evidence_summary="evidence",
            classifier_version="1.0.0",
        )

    c0 = _make_classification(0.0)
    assert c0.confidence_score == 0.0

    c1 = _make_classification(1.0)
    assert c1.confidence_score == 1.0

    with pytest.raises(ValidationError):
        _make_classification(-0.1)

    with pytest.raises(ValidationError):
        _make_classification(1.1)


def test_failure_mode_classification_empty_secondary_modes() -> None:
    """Verify empty tuple is valid for secondary_modes."""
    classification = FailureModeClassification(
        classification_id="fmc-test",
        run_id="run-1",
        session_id="sess-1",
        workspace_id="ws-1",
        primary_mode=FailureMode.SPEC_DRIFT,
        confidence_score=0.5,
        evidence_summary="evidence",
        secondary_modes=(),
        classifier_version="1.0.0",
    )
    assert classification.secondary_modes == ()
