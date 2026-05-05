# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.roles.memory_models import RolePerformanceMetrics
from relay_teams.validation import RequiredIdentifierStr


class MaturityLevel(str, Enum):
    L1_REACTIVE = "L1"
    L2_TASK_ORIENTED = "L2"
    L3_CONTEXT_AWARE = "L3"
    L4_STRATEGIC = "L4"
    L5_AUTONOMOUS = "L5"


class MaturityScoreEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    factor: str = Field(min_length=1)
    value: str = Field(min_length=1)
    passed: bool


class MaturityScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    level: MaturityLevel
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: tuple[MaturityScoreEvidence, ...] = ()
    scored_at: datetime
    previous_level: MaturityLevel | None = None
    metrics_snapshot: RolePerformanceMetrics | None = None


class MaturityScoringEngine:
    """Deterministic, rule-based L1-L5 maturity scoring per spec section 6.6."""

    @staticmethod
    def score_maturity(
        *,
        role_id: str,
        workspace_id: str,
        performance: RolePerformanceMetrics,
        applied_adjustment_count: int,
    ) -> MaturityScore:
        pass_rate = performance.verification_pass_rate.pass_rate
        total_tasks = performance.task_counts.total_tasks
        has_trend = len(performance.trend) >= 3
        scored_at = datetime.now(tz=timezone.utc)

        previous_level: MaturityLevel | None = None

        if pass_rate < 0.30 or total_tasks < 5:
            level = MaturityLevel.L1_REACTIVE
            confidence = 0.9
            evidence = _build_evidence(
                pass_rate=pass_rate,
                total_tasks=total_tasks,
                has_trend=False,
                adjustment_count=applied_adjustment_count,
                for_level=level,
            )
        elif pass_rate < 0.50:
            level = MaturityLevel.L2_TASK_ORIENTED
            confidence = 0.7
            evidence = _build_evidence(
                pass_rate=pass_rate,
                total_tasks=total_tasks,
                has_trend=False,
                adjustment_count=applied_adjustment_count,
                for_level=level,
            )
        elif pass_rate < 0.70 or not has_trend:
            level = MaturityLevel.L3_CONTEXT_AWARE
            confidence = 0.6 if has_trend else 0.4
            evidence = _build_evidence(
                pass_rate=pass_rate,
                total_tasks=total_tasks,
                has_trend=has_trend,
                adjustment_count=applied_adjustment_count,
                for_level=level,
            )
        elif pass_rate < 0.90 or applied_adjustment_count < 1:
            level = MaturityLevel.L4_STRATEGIC
            confidence = 0.7 if applied_adjustment_count >= 1 else 0.5
            evidence = _build_evidence(
                pass_rate=pass_rate,
                total_tasks=total_tasks,
                has_trend=has_trend,
                adjustment_count=applied_adjustment_count,
                for_level=level,
            )
        else:
            level = MaturityLevel.L5_AUTONOMOUS
            confidence = 0.85 if applied_adjustment_count >= 2 else 0.6
            evidence = _build_evidence(
                pass_rate=pass_rate,
                total_tasks=total_tasks,
                has_trend=has_trend,
                adjustment_count=applied_adjustment_count,
                for_level=level,
            )

        return MaturityScore(
            role_id=role_id,
            workspace_id=workspace_id,
            level=level,
            confidence=confidence,
            evidence=evidence,
            scored_at=scored_at,
            previous_level=previous_level,
            metrics_snapshot=performance,
        )


def _build_evidence(
    *,
    pass_rate: float,
    total_tasks: int,
    has_trend: bool,
    adjustment_count: int,
    for_level: MaturityLevel,
) -> tuple[MaturityScoreEvidence, ...]:
    items: list[MaturityScoreEvidence] = [
        MaturityScoreEvidence(
            factor="verification_pass_rate",
            value=f"{pass_rate:.1%}",
            passed=_pass_rate_check(pass_rate, for_level),
        ),
        MaturityScoreEvidence(
            factor="task_count",
            value=f"{total_tasks} tasks",
            passed=total_tasks >= 5,
        ),
    ]

    if for_level in (
        MaturityLevel.L3_CONTEXT_AWARE,
        MaturityLevel.L4_STRATEGIC,
        MaturityLevel.L5_AUTONOMOUS,
    ):
        items.append(
            MaturityScoreEvidence(
                factor="trend_data",
                value=f"{'available' if has_trend else 'insufficient'}",
                passed=has_trend,
            )
        )

    if for_level in (MaturityLevel.L4_STRATEGIC, MaturityLevel.L5_AUTONOMOUS):
        items.append(
            MaturityScoreEvidence(
                factor="prompt_adjustments",
                value=f"{adjustment_count} applied",
                passed=adjustment_count >= 1,
            )
        )

    return tuple(items)


def _pass_rate_check(pass_rate: float, level: MaturityLevel) -> bool:
    thresholds = {
        MaturityLevel.L1_REACTIVE: 0.0,
        MaturityLevel.L2_TASK_ORIENTED: 0.30,
        MaturityLevel.L3_CONTEXT_AWARE: 0.50,
        MaturityLevel.L4_STRATEGIC: 0.70,
        MaturityLevel.L5_AUTONOMOUS: 0.90,
    }
    return pass_rate >= thresholds.get(level, 0.0)
