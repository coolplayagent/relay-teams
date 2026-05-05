# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.agents.evaluation.distribution_analyzer import DistributionAnalyzer
from relay_teams.agents.evaluation.failure_modes import (
    FailureMode,
    FailureModeClassification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_classification(
    classification_id: str,
    run_id: str,
    primary: FailureMode,
    *,
    secondary: tuple[FailureMode, ...] = (),
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
        secondary_modes=secondary,
        confidence_score=confidence,
        evidence_summary="Test evidence",
        classifier_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_distribution_all_modes_present() -> None:
    """All five modes present in result keys with correct counts."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-b", "run-b", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-c", "run-c", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-d", "run-d", FailureMode.SPEC_DRIFT),
        _make_classification("fmc-e", "run-e", FailureMode.PERMISSION_FRICTION),
        _make_classification("fmc-f", "run-f", FailureMode.VERIFICATION_MISS),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=100,
    )

    assert FailureMode.CONTEXT_ROT in report.failure_distribution
    assert FailureMode.TOOL_SPRAWL in report.failure_distribution
    assert FailureMode.SPEC_DRIFT in report.failure_distribution
    assert FailureMode.PERMISSION_FRICTION in report.failure_distribution
    assert FailureMode.VERIFICATION_MISS in report.failure_distribution

    assert report.failure_distribution[FailureMode.CONTEXT_ROT] == 2
    assert report.failure_distribution[FailureMode.TOOL_SPRAWL] == 1
    assert report.failure_distribution[FailureMode.SPEC_DRIFT] == 1
    assert report.failure_distribution[FailureMode.PERMISSION_FRICTION] == 1
    assert report.failure_distribution[FailureMode.VERIFICATION_MISS] == 1


def test_distribution_percentages_sum_to_100() -> None:
    """Verify percentage sum is approximately 100.0."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-b", "run-b", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-c", "run-c", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-d", "run-d", FailureMode.SPEC_DRIFT),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=50,
    )

    total_pct = sum(report.failure_mode_percentages.values())
    assert abs(total_pct - 100.0) <= 0.5


def test_distribution_priorities_sorted() -> None:
    """Verify descending order by prevalence."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-b", "run-b", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-c", "run-c", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-d", "run-d", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-e", "run-e", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-f", "run-f", FailureMode.VERIFICATION_MISS),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=50,
    )

    priorities = report.harness_layer_priorities
    for i in range(len(priorities) - 1):
        assert priorities[i].prevalence_pct >= priorities[i + 1].prevalence_pct


def test_distribution_single_mode() -> None:
    """All classifications same mode -> top priority matches and has 100%."""
    classifications: tuple[FailureModeClassification, ...] = tuple(
        _make_classification(f"fmc-{i}", f"run-{i}", FailureMode.TOOL_SPRAWL)
        for i in range(5)
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=10,
    )

    # Top priority should be tool_sprawl with ~100%
    assert report.harness_layer_priorities[0].failure_mode == FailureMode.TOOL_SPRAWL
    assert report.harness_layer_priorities[0].prevalence_pct > 90.0


def test_distribution_empty_classifications() -> None:
    """Empty input -> report with sample_size=0 but model requires ge=1.
    The analyzer uses max(1, total) for sample_size when total=0."""
    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=(),
        total_runs_available=0,
    )

    # With empty input, sample_size is set to max(0, 1) = 1 based on code
    assert report.sample_size == 1
    assert report.total_runs_available == 0
    assert report.multi_mode_rate == 0.0
    assert len(report.classifications) == 0


def test_distribution_multi_mode_rate() -> None:
    """Mix of single and multi-mode -> correct multi_mode_rate."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification(
            "fmc-a",
            "run-a",
            FailureMode.CONTEXT_ROT,
            secondary=(FailureMode.TOOL_SPRAWL,),
        ),
        _make_classification(
            "fmc-b",
            "run-b",
            FailureMode.TOOL_SPRAWL,
            secondary=(FailureMode.SPEC_DRIFT,),
        ),
        _make_classification("fmc-c", "run-c", FailureMode.SPEC_DRIFT),
        _make_classification("fmc-d", "run-d", FailureMode.PERMISSION_FRICTION),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=50,
    )

    # 2 out of 4 have secondary modes = 0.5
    assert report.multi_mode_rate == 0.5


def test_distribution_summary_nonempty() -> None:
    """Summary string is non-empty."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-b", "run-b", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-c", "run-c", FailureMode.TOOL_SPRAWL),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=10,
    )

    assert len(report.summary) > 0
    assert "context rot" in report.summary.lower()


def test_distribution_harness_mapping() -> None:
    """Each failure mode maps to correct harness layer."""
    classifications: tuple[FailureModeClassification, ...] = (
        _make_classification("fmc-a", "run-a", FailureMode.CONTEXT_ROT),
        _make_classification("fmc-b", "run-b", FailureMode.TOOL_SPRAWL),
        _make_classification("fmc-c", "run-c", FailureMode.SPEC_DRIFT),
        _make_classification("fmc-d", "run-d", FailureMode.PERMISSION_FRICTION),
        _make_classification("fmc-e", "run-e", FailureMode.VERIFICATION_MISS),
    )

    analyzer = DistributionAnalyzer()
    report = analyzer.analyze(
        classifications=classifications,
        total_runs_available=10,
    )

    layer_by_mode: dict[FailureMode, str] = {}
    for item in report.harness_layer_priorities:
        layer_by_mode[item.failure_mode] = item.harness_layer

    assert layer_by_mode[FailureMode.CONTEXT_ROT] == "context_engineering"
    assert layer_by_mode[FailureMode.TOOL_SPRAWL] == "tool_policy"
    assert layer_by_mode[FailureMode.SPEC_DRIFT] == "spec_drift_detection"
    assert layer_by_mode[FailureMode.PERMISSION_FRICTION] == "permission_gate"
    assert layer_by_mode[FailureMode.VERIFICATION_MISS] == "verification"
