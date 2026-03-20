from __future__ import annotations

import pytest

from agent_teams_evals.models import AuxiliaryScore, EvalResult, RunOutcome, TokenUsage
from agent_teams_evals.reporter import build_report


def test_build_report_aggregates_auxiliary_scores() -> None:
    results = [
        EvalResult(
            item_id="a",
            dataset="swebench",
            run_id="run-1",
            session_id="session-1",
            outcome=RunOutcome.COMPLETED,
            passed=True,
            score=1.0,
            scorer_name="swebench_docker",
            auxiliary_scores={
                "patch_jaccard": AuxiliaryScore(score=0.25, passed=False)
            },
            token_usage=TokenUsage(
                input_tokens=10,
                cached_input_tokens=4,
                output_tokens=5,
                reasoning_output_tokens=2,
                total_tokens=15,
                total_requests=1,
                total_tool_calls=2,
            ),
        ),
        EvalResult(
            item_id="b",
            dataset="swebench",
            run_id="run-2",
            session_id="session-2",
            outcome=RunOutcome.COMPLETED,
            passed=False,
            score=0.0,
            scorer_name="swebench_docker",
            auxiliary_scores={
                "patch_jaccard": AuxiliaryScore(score=0.75, passed=False)
            },
            token_usage=TokenUsage(
                input_tokens=20,
                cached_input_tokens=6,
                output_tokens=10,
                reasoning_output_tokens=3,
                total_tokens=30,
                total_requests=4,
                total_tool_calls=1,
            ),
        ),
    ]

    report = build_report(
        results,
        dataset="swebench",
        scorer_name="swebench_docker",
        cost_per_million_input=1_000.0,
        cost_per_million_cached_input=100.0,
        cost_per_million_output=2_000.0,
        cost_per_million_reasoning_output=3_000.0,
    )

    assert report.mean_score == 0.5
    assert report.auxiliary_score_means["patch_jaccard"] == 0.5
    assert report.total_input_tokens == 30
    assert report.total_cached_input_tokens == 10
    assert report.total_output_tokens == 15
    assert report.total_reasoning_output_tokens == 5
    assert report.total_requests == 5
    assert report.total_tool_calls == 3
    assert report.estimated_input_cost_usd == pytest.approx(0.03)
    assert report.estimated_cached_input_cost_usd == pytest.approx(0.001)
    assert report.estimated_output_cost_usd == pytest.approx(0.03)
    assert report.estimated_reasoning_output_cost_usd == pytest.approx(0.015)
    assert report.estimated_cost_usd == pytest.approx(0.076)
