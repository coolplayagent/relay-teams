from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent_teams_evals.models import (
    AuxiliaryScore,
    EvalResult,
    RunOutcome,
    SWEBenchDiagnostics,
    SWEBenchResolutionStatus,
    SWEBenchTestBucket,
    SWEBenchTestsStatus,
    TokenUsage,
)
from agent_teams_evals.reporter import EvalReporter, _format_usage_cell, build_report


def _local_tmp_dir(name: str) -> Path:
    path = Path(".tmp/agent_teams_evals_tests") / f"{name}-{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def test_format_usage_cell_uses_readable_labels() -> None:
    result = EvalResult(
        item_id="demo",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=True,
        score=1.0,
        scorer_name="swebench_docker",
        token_usage=TokenUsage(
            input_tokens=1_209_700,
            cached_input_tokens=1_076_200,
            output_tokens=13_900,
            reasoning_output_tokens=4_700,
            total_tokens=1_223_600,
            total_requests=95,
            total_tool_calls=113,
        ),
    )

    assert _format_usage_cell(result) == (
        "input=1209.7k cached=1076.2k output=13.9k "
        "reasoning=4.7k requests=95 tool_calls=113"
    )


def test_write_json_includes_swebench_diagnostics() -> None:
    report = build_report(
        [
            EvalResult(
                item_id="demo",
                dataset="swebench",
                run_id="run-1",
                session_id="session-1",
                outcome=RunOutcome.COMPLETED,
                passed=False,
                score=0.0,
                scorer_name="swebench_docker",
                swebench_diagnostics=SWEBenchDiagnostics(
                    completed=True,
                    resolved=False,
                    resolution_status=SWEBenchResolutionStatus.PARTIAL,
                    patch_exists=True,
                    patch_successfully_applied=True,
                    tests_status=SWEBenchTestsStatus(
                        fail_to_pass=SWEBenchTestBucket(
                            success=("tests/test_fix.py::test_fix",),
                            failure=("tests/test_fix.py::test_other",),
                        ),
                        pass_to_pass=SWEBenchTestBucket(
                            success=("tests/test_keep.py::test_keep",),
                        ),
                    ),
                ),
                token_usage=TokenUsage(),
            )
        ],
        dataset="swebench",
        scorer_name="swebench_docker",
    )
    path = _local_tmp_dir("reporter-json") / "report.json"

    EvalReporter().write_json(report, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    diagnostics = payload["results"][0]["swebench_diagnostics"]
    assert diagnostics["resolution_status"] == "partial"
    assert diagnostics["patch_exists"] is True
    assert diagnostics["tests_status"]["fail_to_pass"]["success"] == [
        "tests/test_fix.py::test_fix"
    ]


def test_write_html_includes_rerun_command() -> None:
    report = build_report(
        [
            EvalResult(
                item_id="demo",
                dataset="swebench",
                run_id="run-1",
                session_id="session-1",
                outcome=RunOutcome.FAILED,
                passed=False,
                score=0.0,
                scorer_name="swebench_docker",
                error="docker run failed",
                rerun_command=(
                    "uv run agent-teams-evals run --config 'eval.yaml' "
                    "--item-ids 'demo' --rerun"
                ),
                token_usage=TokenUsage(),
            )
        ],
        dataset="swebench",
        scorer_name="swebench_docker",
    )
    path = _local_tmp_dir("reporter-html") / "report.html"

    EvalReporter().write_html(report, path)

    html = path.read_text(encoding="utf-8")
    assert "<th>rerun</th>" in html
    assert "--item-ids 'demo' --rerun" in html
