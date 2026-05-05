# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)
from relay_teams.agents.evaluation.mvh_report import (
    HarnessPriorityItem,
    MVHRecommendationReport,
)


def _make_classification(
    classification_id: str,
    run_id: str,
    primary: FailureMode,
    *,
    session_id: str = "sess-1",
    workspace_id: str = "ws-1",
    confidence: float = 0.8,
) -> FailureModeClassification:
    return FailureModeClassification(
        classification_id=classification_id,
        run_id=run_id,
        session_id=session_id,
        workspace_id=workspace_id,
        primary_mode=primary,
        confidence_score=confidence,
        evidence_summary="Test evidence",
        classifier_version="1.0.0",
    )


def test_mvh_report_creation() -> None:
    """Instantiate MVHRecommendationReport with complete data."""
    now = datetime.now(timezone.utc)
    cls1 = _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT)
    cls2 = _make_classification("fmc-b", "run-b", FailureMode.TOOL_SPRAWL)

    report = MVHRecommendationReport(
        report_id="mvh-test",
        generated_at=now,
        sample_size=2,
        total_runs_available=100,
        failure_distribution={
            FailureMode.CONTEXT_ROT: 1,
            FailureMode.TOOL_SPRAWL: 1,
            FailureMode.SPEC_DRIFT: 0,
            FailureMode.PERMISSION_FRICTION: 0,
            FailureMode.VERIFICATION_MISS: 0,
        },
        failure_mode_percentages={
            FailureMode.CONTEXT_ROT: 50.0,
            FailureMode.TOOL_SPRAWL: 50.0,
            FailureMode.SPEC_DRIFT: 0.0,
            FailureMode.PERMISSION_FRICTION: 0.0,
            FailureMode.VERIFICATION_MISS: 0.0,
        },
        multi_mode_rate=0.0,
        harness_layer_priorities=(
            HarnessPriorityItem(
                rank=1,
                harness_layer="context_engineering",
                failure_mode=FailureMode.CONTEXT_ROT,
                prevalence_pct=50.0,
                recommended_action="Invest in context engineering",
            ),
        ),
        summary="Test summary",
        classifications=(cls1, cls2),
    )
    assert report.report_id == "mvh-test"
    assert report.generated_at == now
    assert report.sample_size == 2
    assert report.total_runs_available == 100
    assert report.failure_distribution[FailureMode.CONTEXT_ROT] == 1
    assert report.failure_mode_percentages[FailureMode.CONTEXT_ROT] == 50.0
    assert report.multi_mode_rate == 0.0
    assert len(report.harness_layer_priorities) == 1
    assert report.summary == "Test summary"
    assert len(report.classifications) == 2


def test_mvh_report_validation() -> None:
    """Missing required fields raise ValidationError."""
    with pytest.raises(ValidationError):
        MVHRecommendationReport.model_validate({"report_id": "mvh-test"})


def test_mvh_report_rank_sequential() -> None:
    """HarnessPriorityItem ranks are sequential."""
    items = (
        HarnessPriorityItem(
            rank=1,
            harness_layer="context_engineering",
            failure_mode=FailureMode.CONTEXT_ROT,
            prevalence_pct=50.0,
            recommended_action="Action 1",
        ),
        HarnessPriorityItem(
            rank=2,
            harness_layer="tool_policy",
            failure_mode=FailureMode.TOOL_SPRAWL,
            prevalence_pct=30.0,
            recommended_action="Action 2",
        ),
        HarnessPriorityItem(
            rank=3,
            harness_layer="verification",
            failure_mode=FailureMode.VERIFICATION_MISS,
            prevalence_pct=20.0,
            recommended_action="Action 3",
        ),
    )
    assert items[0].rank == 1
    assert items[1].rank == 2
    assert items[2].rank == 3
