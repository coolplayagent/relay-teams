# -*- coding: utf-8 -*-
"""Coverage for llm_evaluator.py evaluate_role_performance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator


def _make_provider(
    response_text: str = '{"scores":[{"dimension":"quality","score":4,"reasoning":"ok"}],"overall_score":4.0,"summary":"ok"}',
) -> MagicMock:
    provider = MagicMock()
    # _run_evaluation calls await provider.generate(request), then passes
    # the return value to _parse_llm_response(response) which expects a str
    provider.generate = AsyncMock(return_value=response_text)
    return provider


def _make_evaluator(provider: MagicMock | None = None) -> LLMEvaluator:
    return LLMEvaluator(
        provider=provider or _make_provider(),
        model="test-model",
        run_id="r1",
        trace_id="t1",
        task_id="task1",
        session_id="s1",
        workspace_id="w1",
        instance_id="i1",
        role_id="role1",
    )


@pytest.mark.asyncio
async def test_evaluate_role_performance_normal() -> None:
    evaluator = _make_evaluator()
    from relay_teams.roles.memory_models import (
        RolePerformanceMetrics,
        VerificationPassRate,
        RoleTaskCounts,
    )

    perf = RolePerformanceMetrics(
        role_id="r1",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=10, passed_verifications=7, pass_rate=0.7
        ),
        task_counts=RoleTaskCounts(total_tasks=10, successful_tasks=7, failed_tasks=3),
    )
    result = await evaluator.evaluate_role_performance(
        role_id="r1",
        current_system_prompt="You are a helpful assistant.",
        performance=perf,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_evaluate_role_performance_fallback() -> None:
    provider = MagicMock()
    provider.generate = AsyncMock(side_effect=RuntimeError("API down"))
    evaluator = _make_evaluator(provider=provider)

    from relay_teams.roles.memory_models import (
        RolePerformanceMetrics,
        VerificationPassRate,
        RoleTaskCounts,
    )

    perf = RolePerformanceMetrics(
        role_id="r1",
        workspace_id="w1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=5, passed_verifications=5, pass_rate=1.0
        ),
        task_counts=RoleTaskCounts(total_tasks=5, successful_tasks=5, failed_tasks=0),
    )
    result = await evaluator.evaluate_role_performance(
        role_id="r1",
        current_system_prompt="prompt",
        performance=perf,
    )
    assert result is not None
    assert result.fallback is True
