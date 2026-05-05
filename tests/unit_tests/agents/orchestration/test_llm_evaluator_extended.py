# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.providers.provider_contracts import LLMProvider
from relay_teams.roles.memory_models import (
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)


def _make_evaluator(provider: LLMProvider | None = None) -> LLMEvaluator:
    return LLMEvaluator(
        provider=provider or MagicMock(spec=LLMProvider),
        model="test-model",
        run_id="r1",
        trace_id="t1",
        task_id="task1",
        session_id="s1",
        workspace_id="w1",
        instance_id="i1",
        role_id="role1",
    )


def _make_performance() -> RolePerformanceMetrics:
    return RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=10,
            passed_verifications=7,
            pass_rate=0.7,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=10,
            successful_tasks=7,
            failed_tasks=3,
        ),
        average_verification_score=3.5,
    )


@pytest.mark.asyncio
async def test_evaluate_role_performance_normal() -> None:
    perf = _make_performance()
    llm_response = json.dumps(
        {
            "scores": [
                {
                    "dimension": "strategy",
                    "score": 4,
                    "reasoning": "Clear strategy section",
                },
            ],
            "summary": "The role performs well but could improve tool usage guidance.",
            "recommendations": [
                "strategy: Add specific guidance on when to use each tool.",
            ],
        }
    )

    provider = MagicMock(spec=LLMProvider)
    provider.generate = AsyncMock(return_value=llm_response)
    evaluator = _make_evaluator(provider)

    result = await evaluator.evaluate_role_performance(
        role_id="test-role",
        current_system_prompt="## strategy\nBe helpful",
        performance=perf,
    )
    assert result.overall_score > 0
    assert len(result.scores) == 1
    assert result.scores[0].dimension == "strategy"
    assert result.scores[0].score == 4
    assert "performs well" in result.summary
    assert len(result.recommendations) == 1
    assert result.fallback is False


@pytest.mark.asyncio
async def test_evaluate_role_performance_fallback() -> None:
    perf = _make_performance()

    provider = MagicMock(spec=LLMProvider)
    provider.generate = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    evaluator = _make_evaluator(provider)

    result = await evaluator.evaluate_role_performance(
        role_id="test-role",
        current_system_prompt="## strategy\nBe helpful",
        performance=perf,
    )
    assert result.fallback is True
    assert result.evaluator == "rule"
    assert result.overall_score == 3.0
    assert len(result.recommendations) >= 1
