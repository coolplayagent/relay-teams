# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)


def test_verification_pass_rate_computation() -> None:
    vpr = VerificationPassRate(
        total_verifications=10,
        passed_verifications=7,
        pass_rate=0.7,
    )
    assert vpr.total_verifications == 10
    assert vpr.passed_verifications == 7
    assert vpr.pass_rate == 0.7


def test_verification_pass_rate_validation() -> None:
    with pytest.raises(ValidationError):
        VerificationPassRate(
            total_verifications=5,
            passed_verifications=10,
            pass_rate=2.0,
        )


def test_role_task_counts_computation() -> None:
    rtc = RoleTaskCounts(
        total_tasks=10,
        successful_tasks=7,
        failed_tasks=3,
    )
    assert rtc.total_tasks == 10
    assert rtc.successful_tasks == 7
    assert rtc.failed_tasks == 3


def test_role_task_counts_validation() -> None:
    with pytest.raises(ValidationError):
        RoleTaskCounts(
            total_tasks=10,
            successful_tasks=8,
            failed_tasks=5,
        )


def test_performance_trend_point_creation() -> None:
    now = datetime.now(tz=timezone.utc)
    ptp = PerformanceTrendPoint(
        recorded_at=now,
        verification_pass_rate=0.75,
        average_verification_score=3.5,
        total_tasks_at_point=10,
    )
    assert ptp.recorded_at == now
    assert ptp.verification_pass_rate == 0.75
    assert ptp.average_verification_score == 3.5
    assert ptp.total_tasks_at_point == 10


def test_role_performance_metrics_defaults() -> None:
    rpm = RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=0,
            passed_verifications=0,
            pass_rate=0.0,
        ),
        task_counts=RoleTaskCounts(),
    )
    assert rpm.role_id == "test-role"
    assert rpm.workspace_id == "ws-1"
    assert rpm.average_verification_score == 0.0
    assert rpm.trend == ()
    assert rpm.last_evaluated_at is None
