from __future__ import annotations

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
            token_usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
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
            token_usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        ),
    ]

    report = build_report(results, dataset="swebench", scorer_name="swebench_docker")

    assert report.mean_score == 0.5
    assert report.auxiliary_score_means["patch_jaccard"] == 0.5
