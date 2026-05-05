# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from relay_teams.roles.maturity_scoring import (
    MaturityLevel,
    MaturityScoringEngine,
)
from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)


def _make_performance(
    *,
    pass_rate: float,
    total_tasks: int,
    trend_count: int = 0,
    successful_tasks: int = 0,
    failed_tasks: int = 0,
) -> RolePerformanceMetrics:
    if successful_tasks == 0 and failed_tasks == 0:
        successful_tasks = total_tasks // 2
        failed_tasks = total_tasks - successful_tasks
    total_verifications = max(total_tasks, 1)
    passed_verifications = int(pass_rate * total_verifications)
    trend: tuple[PerformanceTrendPoint, ...] = ()
    if trend_count > 0:
        now = datetime.now(tz=timezone.utc)
        trend = tuple(
            PerformanceTrendPoint(
                recorded_at=now,
                verification_pass_rate=pass_rate,
                average_verification_score=3.0,
                total_tasks_at_point=total_tasks,
            )
            for _ in range(trend_count)
        )
    return RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=total_verifications,
            passed_verifications=passed_verifications,
            pass_rate=pass_rate,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=total_tasks,
            successful_tasks=successful_tasks,
            failed_tasks=failed_tasks,
        ),
        average_verification_score=3.0,
        trend=trend,
    )


def test_score_l1_low_pass_rate() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.2, total_tasks=10)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=0,
    )
    assert score.level == MaturityLevel.L1_REACTIVE
    assert score.confidence == 0.9


def test_score_l1_few_tasks() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=1.0, total_tasks=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=5,
    )
    assert score.level == MaturityLevel.L1_REACTIVE


def test_score_l2() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.40, total_tasks=10)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=0,
    )
    assert score.level == MaturityLevel.L2_TASK_ORIENTED
    assert score.confidence == 0.7


def test_score_l3() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.60, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=0,
    )
    assert score.level == MaturityLevel.L3_CONTEXT_AWARE
    assert score.confidence == 0.6


def test_score_l4() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.80, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=2,
    )
    assert score.level == MaturityLevel.L4_STRATEGIC
    assert score.confidence == 0.7


def test_score_l5() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.95, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=2,
    )
    assert score.level == MaturityLevel.L5_AUTONOMOUS
    assert score.confidence == 0.85


def test_score_boundary_30() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.30, total_tasks=10)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=0,
    )
    assert score.level == MaturityLevel.L2_TASK_ORIENTED


def test_score_boundary_50() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.50, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=0,
    )
    assert score.level == MaturityLevel.L3_CONTEXT_AWARE


def test_score_boundary_70() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.70, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=1,
    )
    assert score.level == MaturityLevel.L4_STRATEGIC


def test_score_boundary_90() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.90, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=2,
    )
    assert score.level == MaturityLevel.L5_AUTONOMOUS


def test_evidence_contains_all_factors() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.80, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=2,
    )
    factor_names = {e.factor for e in score.evidence}
    assert "verification_pass_rate" in factor_names
    assert "task_count" in factor_names
    assert "trend_data" in factor_names
    assert "prompt_adjustments" in factor_names


def test_previous_level_populated() -> None:
    engine = MaturityScoringEngine()
    perf = _make_performance(pass_rate=0.95, total_tasks=10, trend_count=3)
    score = engine.score_maturity(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
        applied_adjustment_count=2,
    )
    assert score.previous_level is None
    assert score.metrics_snapshot is perf
