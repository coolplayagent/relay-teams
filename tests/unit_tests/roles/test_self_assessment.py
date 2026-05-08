# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationResult,
    LLMEvaluationScore,
)
from relay_teams.roles.memory_models import (
    RoleMemoryRecord,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.self_assessment_service import (
    RoleSelfAssessmentService,
    SelfAssessmentConfig,
)


@pytest.fixture
def mock_llm_evaluator() -> MagicMock:
    evaluator = MagicMock()
    evaluator.evaluate_role_performance = AsyncMock()
    return evaluator


@pytest.fixture
def mock_role_memory_service() -> MagicMock:
    service = MagicMock()
    service.get_reflection_record_async = AsyncMock()
    return service


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


@pytest.mark.asyncio
async def test_maybe_assess_disabled(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(enabled=False)
    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=20,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_below_run_threshold(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(trigger_every_n_runs=10, enabled=True)
    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=5,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_below_task_threshold(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(
        trigger_every_n_runs=10,
        min_tasks_for_assessment=5,
    )
    perf = _make_performance(pass_rate=0.5, total_tasks=3)
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )
    mock_role_memory_service.get_reflection_record_async.return_value = record

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_success(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(
        trigger_every_n_runs=10,
        min_tasks_for_assessment=5,
    )
    perf = _make_performance(pass_rate=0.6, total_tasks=10)
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )
    mock_role_memory_service.get_reflection_record_async.return_value = record

    mock_llm_evaluator.evaluate_role_performance.return_value = LLMEvaluationResult(
        scores=[
            LLMEvaluationScore(dimension="clarity", score=4, reasoning="good"),
        ],
        overall_score=4.0,
        summary="The role performs well but needs better instructions.",
        recommendations=["strategy: Use more specific instructions for tool usage."],
    )

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="## strategy\nBe helpful",
        run_count_since_last=15,
    )
    assert result is not None
    assert result.role_id == "test-role"
    assert result.workspace_id == "ws-1"
    assert (
        result.overall_assessment
        == "The role performs well but needs better instructions."
    )
    assert len(result.recommendations) == 1
    assert result.recommendations[0].target_section == "strategy"
    assert result.metrics_snapshot is perf
    assert result.assessment_version == 1


@pytest.mark.asyncio
async def test_self_assessment_result_stored(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(
        trigger_every_n_runs=10,
        min_tasks_for_assessment=5,
    )
    perf = _make_performance(pass_rate=0.6, total_tasks=10)
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )
    mock_role_memory_service.get_reflection_record_async.return_value = record

    mock_llm_evaluator.evaluate_role_performance.return_value = LLMEvaluationResult(
        scores=[
            LLMEvaluationScore(dimension="completeness", score=3, reasoning="ok"),
        ],
        overall_score=3.0,
        summary="Assessment done.",
        recommendations=["strategy: Improve instructions."],
    )

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="## strategy\nBe helpful",
        run_count_since_last=15,
    )
    assert result is not None
    assert result.role_id == "test-role"
    assert result.workspace_id == "ws-1"
    assert len(result.recommendations) == 1

    mock_llm_evaluator.evaluate_role_performance.assert_awaited_once()


@pytest.mark.asyncio
async def test_maybe_assess_performance_none(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(trigger_every_n_runs=10, enabled=True)
    record = RoleMemoryRecord(role_id="test-role", workspace_id="ws-1")
    assert record.performance is None
    mock_role_memory_service.get_reflection_record_async.return_value = record

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_assess_llm_fallback(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(
        trigger_every_n_runs=10,
        min_tasks_for_assessment=5,
    )
    perf = _make_performance(pass_rate=0.6, total_tasks=10)
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )
    mock_role_memory_service.get_reflection_record_async.return_value = record
    mock_llm_evaluator.evaluate_role_performance.side_effect = OSError("API down")

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is not None
    assert (
        "LLM evaluation unavailable" in result.overall_assessment
        or result.overall_assessment != ""
    )
    assert len(result.recommendations) >= 1


@pytest.mark.asyncio
async def test_maybe_assess_empty_recommendations(
    mock_llm_evaluator: MagicMock,
    mock_role_memory_service: MagicMock,
) -> None:
    config = SelfAssessmentConfig(
        trigger_every_n_runs=10,
        min_tasks_for_assessment=5,
    )
    perf = _make_performance(pass_rate=0.6, total_tasks=10)
    record = RoleMemoryRecord(
        role_id="test-role",
        workspace_id="ws-1",
        performance=perf,
    )
    mock_role_memory_service.get_reflection_record_async.return_value = record
    mock_llm_evaluator.evaluate_role_performance.return_value = LLMEvaluationResult(
        scores=[],
        overall_score=3.0,
        summary="",
        recommendations=["  ", ""],
    )

    service = RoleSelfAssessmentService(
        llm_evaluator=mock_llm_evaluator,
        role_memory_service=mock_role_memory_service,
        config=config,
    )
    result = await service.maybe_assess(
        role_id="test-role",
        workspace_id="ws-1",
        current_system_prompt="prompt",
        run_count_since_last=15,
    )
    assert result is not None
    assert result.overall_assessment == "Self-assessment completed."
    assert len(result.recommendations) == 0
