# -*- coding: utf-8 -*-
"""Coverage for memory_service.py record_verification_outcome."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.roles.memory_models import (
    RoleMemoryRecord,
    RolePerformanceMetrics,
    VerificationPassRate,
    RoleTaskCounts,
)
from relay_teams.roles.memory_service import RoleMemoryService


def _make_service() -> tuple[RoleMemoryService, MagicMock]:
    repo = MagicMock()
    return RoleMemoryService(repository=repo), repo


def _base_record() -> RoleMemoryRecord:
    return RoleMemoryRecord(
        role_id="r1",
        workspace_id="w1",
        content_markdown="",
        performance=RolePerformanceMetrics(
            role_id="r1",
            workspace_id="w1",
            verification_pass_rate=VerificationPassRate(
                total_verifications=5, passed_verifications=4, pass_rate=0.8
            ),
            task_counts=RoleTaskCounts(
                total_tasks=5, successful_tasks=4, failed_tasks=1
            ),
            average_verification_score=3.0,
        ),
    )


@pytest.mark.asyncio
async def test_record_verification_outcome_with_existing_score() -> None:
    svc, repo = _make_service()
    base = _base_record()
    # First read returns base, second read (after write) returns updated
    updated = base.model_copy(
        update={
            "performance": RolePerformanceMetrics(
                role_id="r1",
                workspace_id="w1",
                verification_pass_rate=VerificationPassRate(
                    total_verifications=6, passed_verifications=5, pass_rate=5.0 / 6.0
                ),
                task_counts=RoleTaskCounts(
                    total_tasks=6, successful_tasks=5, failed_tasks=1
                ),
                average_verification_score=3.33,
            ),
        }
    )
    repo.read_role_memory_async = AsyncMock(side_effect=[base, updated])
    repo.write_role_memory_async = AsyncMock()

    vr = MagicMock()
    vr.passed = True
    vr.checks = [MagicMock(score=4.0), MagicMock(score=5.0)]

    result = await svc.record_verification_outcome(
        role_id="r1", workspace_id="w1", verification_report=vr
    )
    assert result.performance is not None
    assert result.performance.task_counts.total_tasks == 6
    assert result.performance.task_counts.successful_tasks == 5
    repo.write_role_memory_async.assert_called_once()


@pytest.mark.asyncio
async def test_record_verification_outcome_no_performance() -> None:
    svc, repo = _make_service()
    initial = RoleMemoryRecord(role_id="r1", workspace_id="w1", performance=None)
    # After write, second read returns updated record
    updated = RoleMemoryRecord(
        role_id="r1",
        workspace_id="w1",
        performance=RolePerformanceMetrics(
            role_id="r1",
            workspace_id="w1",
            verification_pass_rate=VerificationPassRate(
                total_verifications=1, passed_verifications=0, pass_rate=0.0
            ),
            task_counts=RoleTaskCounts(
                total_tasks=1, successful_tasks=0, failed_tasks=1
            ),
            average_verification_score=0.0,
        ),
    )
    repo.read_role_memory_async = AsyncMock(side_effect=[initial, updated])
    repo.write_role_memory_async = AsyncMock()

    vr = MagicMock()
    vr.passed = False
    vr.checks = [MagicMock(score=2.0)]

    result = await svc.record_verification_outcome(
        role_id="r1", workspace_id="w1", verification_report=vr
    )
    assert result.performance is not None
    assert result.performance.task_counts.total_tasks == 1
    assert result.performance.task_counts.failed_tasks == 1
    repo.write_role_memory_async.assert_called_once()
