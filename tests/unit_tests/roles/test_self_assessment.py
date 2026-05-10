# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.memory.models import (
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQueryResult,
    MemoryEntrySummary,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_models import (
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.self_assessment_service import (
    RoleSelfAssessmentService,
    SelfAssessmentConfig,
)


def _make_performance(
    *,
    pass_rate: float = 0.5,
    total_tasks: int = 10,
) -> RolePerformanceMetrics:
    return RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=total_tasks,
            passed_verifications=int(pass_rate * total_tasks),
            pass_rate=pass_rate,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=total_tasks,
            successful_tasks=int(pass_rate * total_tasks),
            failed_tasks=total_tasks - int(pass_rate * total_tasks),
        ),
    )


def _make_memory_service(
    performance: RolePerformanceMetrics | None,
) -> MagicMock:
    service = MagicMock(spec=MemoryBankService)
    now = datetime.now(tz=timezone.utc)
    if performance is None:
        service.list_entries_async = AsyncMock(
            return_value=MemoryQueryResult(items=(), total_count=0, offset=0, limit=20)
        )
        service.get_entry_async = AsyncMock(return_value=None)
        return service

    summary = MemoryEntrySummary(
        id="mem-1",
        tier=MemoryTier.PERSISTENT,
        scope=MemoryScope.ROLE,
        workspace_id="ws-1",
        session_id=None,
        role_id="test-role",
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        content_title="Role performance",
        content_body_preview=performance.model_dump_json(),
        tags=("role-performance",),
        confidence_score=0.9,
        source=MemorySourceKind.CONSOLIDATION,
        version=1,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    entry = MemoryEntry(
        id="mem-1",
        tier=MemoryTier.PERSISTENT,
        scope=MemoryScope.ROLE,
        workspace_id="ws-1",
        role_id="test-role",
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        content=MemoryContent(
            title="Role performance",
            body=performance.model_dump_json(),
        ),
        tags=("role-performance",),
        confidence_score=0.9,
        source=MemorySourceKind.CONSOLIDATION,
        created_at=now,
        updated_at=now,
    )
    service.list_entries_async = AsyncMock(
        return_value=MemoryQueryResult(
            items=(summary,),
            total_count=1,
            offset=0,
            limit=20,
        )
    )
    service.get_entry_async = AsyncMock(return_value=entry)
    return service


@pytest.fixture
def mock_llm_evaluator() -> MagicMock:
    evaluator = MagicMock()
    evaluator.evaluate_role_performance = AsyncMock()
    return evaluator


@pytest.mark.asyncio
async def test_maybe_assess_disabled(mock_llm_evaluator: MagicMock) -> None:
    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        memory_bank_service=_make_memory_service(_make_performance()),
        config=SelfAssessmentConfig(enabled=False),
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=20,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_reads_performance_from_memory_bank(
    mock_llm_evaluator: MagicMock,
) -> None:
    performance = _make_performance(pass_rate=0.6, total_tasks=10)
    mock_llm_evaluator.evaluate_role_performance.return_value = LLMEvaluationResult(
        scores=[
            LLMEvaluationScore(dimension="clarity", score=4, reasoning="good"),
        ],
        overall_score=4.0,
        summary="The role performs well but needs better instructions.",
        recommendations=["Use more specific instructions for tool usage."],
    )

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        memory_bank_service=_make_memory_service(performance),
        config=SelfAssessmentConfig(
            trigger_every_n_runs=10, min_tasks_for_assessment=5
        ),
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="## strategy\nBe helpful",
        run_count_since_last=15,
    )

    assert result is not None
    assert result.metrics_snapshot == performance
    assert result.overall_assessment == (
        "The role performs well but needs better instructions."
    )
    assert len(result.recommendations) == 1
    mock_llm_evaluator.evaluate_role_performance.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_assess_returns_none_without_memory_bank_performance(
    mock_llm_evaluator: MagicMock,
) -> None:
    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        memory_bank_service=_make_memory_service(None),
        config=SelfAssessmentConfig(trigger_every_n_runs=10, enabled=True),
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_llm_fallback(mock_llm_evaluator: MagicMock) -> None:
    mock_llm_evaluator.evaluate_role_performance.side_effect = OSError("API down")
    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        memory_bank_service=_make_memory_service(_make_performance(total_tasks=10)),
        config=SelfAssessmentConfig(
            trigger_every_n_runs=10, min_tasks_for_assessment=5
        ),
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is not None
    assert "LLM evaluation unavailable" in result.overall_assessment
    assert len(result.recommendations) >= 1
