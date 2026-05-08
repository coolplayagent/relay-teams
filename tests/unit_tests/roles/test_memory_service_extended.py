# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relay_teams.agents.tasks.models import (
    VerificationCheckResult,
    VerificationLayer,
    VerificationReport,
)
from relay_teams.roles.memory_models import (
    RoleMemoryRecord,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.memory_repository import RoleMemoryRepository
from relay_teams.roles.memory_service import RoleMemoryService


def _make_passing_report() -> VerificationReport:
    return VerificationReport(
        task_id="task-1",
        passed=True,
        checks=(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name="spec_match",
                passed=True,
                details="ok",
            ),
            VerificationCheckResult(
                layer=VerificationLayer.EVIDENCE,
                name="evidence_present",
                passed=True,
                details="ok",
            ),
        ),
    )


def _make_failing_report() -> VerificationReport:
    return VerificationReport(
        task_id="task-1",
        passed=False,
        checks=(
            VerificationCheckResult(
                layer=VerificationLayer.SPEC,
                name="spec_match",
                passed=False,
                details="mismatch",
            ),
        ),
    )


def _make_service(tmp_path: Path) -> RoleMemoryService:
    return RoleMemoryService(
        repository=RoleMemoryRepository(tmp_path / "role_memory.db")
    )


@pytest.mark.asyncio
async def test_record_verification_outcome_first_write(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    report = _make_passing_report()

    record = await service.record_verification_outcome(
        role_id="test-role",
        workspace_id="ws-1",
        verification_report=report,
    )

    assert record.performance is not None
    assert record.performance.task_counts.total_tasks == 1
    assert record.performance.task_counts.successful_tasks == 1
    assert record.performance.task_counts.failed_tasks == 0
    assert record.performance.verification_pass_rate.total_verifications == 1
    assert record.performance.verification_pass_rate.passed_verifications == 1
    assert len(record.performance.trend) == 1


@pytest.mark.asyncio
async def test_record_verification_outcome_nth_write(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    # Seed 5 tasks: 3 passing, 2 failing
    for i in range(3):
        await service.record_verification_outcome(
            role_id="test-role",
            workspace_id="ws-1",
            verification_report=_make_passing_report(),
        )
    for i in range(2):
        await service.record_verification_outcome(
            role_id="test-role",
            workspace_id="ws-1",
            verification_report=_make_failing_report(),
        )

    # 6th task: passing
    record = await service.record_verification_outcome(
        role_id="test-role",
        workspace_id="ws-1",
        verification_report=_make_passing_report(),
    )

    assert record.performance is not None
    assert record.performance.task_counts.total_tasks == 6
    assert record.performance.task_counts.successful_tasks == 4
    assert record.performance.task_counts.failed_tasks == 2
    assert record.performance.verification_pass_rate.total_verifications == 6
    assert record.performance.verification_pass_rate.passed_verifications == 4


@pytest.mark.asyncio
async def test_record_verification_outcome_trend_trimmed(tmp_path: Path) -> None:
    service = _make_service(tmp_path)

    # Add 25 passing tasks to build a trend larger than 20
    report = _make_passing_report()
    for _ in range(25):
        await service.record_verification_outcome(
            role_id="test-role",
            workspace_id="ws-1",
            verification_report=report,
        )

    record = await service.record_verification_outcome(
        role_id="test-role",
        workspace_id="ws-1",
        verification_report=report,
    )

    assert record.performance is not None
    assert len(record.performance.trend) == 20


@pytest.mark.asyncio
async def test_get_performance_metrics() -> None:
    perf = RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=5,
            passed_verifications=3,
            pass_rate=0.6,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=5,
            successful_tasks=3,
            failed_tasks=2,
        ),
    )
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )

    mock_repo = MagicMock(spec=RoleMemoryRepository)
    mock_repo.read_role_memory_async.return_value = record

    service = RoleMemoryService(repository=mock_repo)
    result = await service.get_performance_metrics_async(
        role_id="test-role",
        workspace_id="ws-1",
    )
    assert result is not None
    assert result.task_counts.total_tasks == 5
    assert result.verification_pass_rate.pass_rate == 0.6


@pytest.mark.asyncio
async def test_get_performance_metrics_none() -> None:
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=None,
    )

    mock_repo = MagicMock(spec=RoleMemoryRepository)
    mock_repo.read_role_memory_async.return_value = record

    service = RoleMemoryService(repository=mock_repo)
    result = await service.get_performance_metrics_async(
        role_id="test-role",
        workspace_id="ws-1",
    )
    assert result is None
