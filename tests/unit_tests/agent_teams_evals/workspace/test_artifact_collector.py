from __future__ import annotations

import json

from agent_teams_evals.models import (
    AuxiliaryScore,
    EvalItem,
    EvalResult,
    RunOutcome,
    TokenUsage,
)
from agent_teams_evals.workspace.artifact_collector import ArtifactCollector


def test_artifact_collector_writes_auxiliary_scores(tmp_path) -> None:
    collector = ArtifactCollector(tmp_path)
    item = EvalItem(item_id="demo", dataset="swebench", intent="demo")
    result = EvalResult(
        item_id="demo",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=True,
        score=1.0,
        scorer_name="swebench_docker",
        auxiliary_scores={
            "patch_jaccard": AuxiliaryScore(score=0.6, passed=False, detail="aux")
        },
        token_usage=TokenUsage(),
    )

    collector.collect(item, result, workspace=None)

    metadata = json.loads(
        (tmp_path / "artifacts" / "demo" / "metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["auxiliary_scores"]["patch_jaccard"]["score"] == 0.6
    assert metadata["auxiliary_scores"]["patch_jaccard"]["detail"] == "aux"
    assert metadata["token_usage"] == TokenUsage().model_dump()


def test_artifact_collector_writes_detailed_token_usage_metadata(tmp_path) -> None:
    collector = ArtifactCollector(tmp_path)
    item = EvalItem(item_id="demo-usage", dataset="swebench", intent="demo")
    usage = TokenUsage(
        input_tokens=10,
        cached_input_tokens=2,
        output_tokens=5,
        reasoning_output_tokens=1,
        total_tokens=15,
        total_requests=3,
        total_tool_calls=4,
    )
    result = EvalResult(
        item_id="demo-usage",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=True,
        score=1.0,
        scorer_name="swebench_docker",
        token_usage=usage,
    )

    collector.collect(item, result, workspace=None)

    metadata = json.loads(
        (tmp_path / "artifacts" / "demo-usage" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    assert metadata["token_usage"] == usage.model_dump()
    assert metadata["input_tokens"] == 10
    assert metadata["output_tokens"] == 5


def test_artifact_collector_writes_filtered_patch_metadata_and_raw_patch(
    tmp_path,
) -> None:
    collector = ArtifactCollector(tmp_path)
    item = EvalItem(item_id="demo-filtered", dataset="swebench", intent="demo")
    result = EvalResult(
        item_id="demo-filtered",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=False,
        score=0.0,
        scorer_name="swebench_docker",
        generated_patch="diff --git a/src/app.py b/src/app.py\n",
        raw_generated_patch=(
            "diff --git a/src/app.py b/src/app.py\n"
            "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        ),
        filtered_generated_files=("tests/test_app.py",),
        token_usage=TokenUsage(),
    )

    collector.collect(item, result, workspace=None)

    artifact_dir = tmp_path / "artifacts" / "demo-filtered"
    metadata = json.loads((artifact_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["filtered_generated_files"] == ["tests/test_app.py"]
    assert (artifact_dir / "patch.diff").read_text(
        encoding="utf-8"
    ) == result.generated_patch
    assert (artifact_dir / "raw_patch.diff").read_text(
        encoding="utf-8"
    ) == result.raw_generated_patch


def test_artifact_collector_writes_scorer_log(tmp_path) -> None:
    collector = ArtifactCollector(tmp_path)
    item = EvalItem(item_id="demo-log", dataset="swebench", intent="demo")
    result = EvalResult(
        item_id="demo-log",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=False,
        score=0.0,
        scorer_name="swebench_docker",
        scorer_log="FAILED tests.test_fix - AssertionError\n1 failed",
        token_usage=TokenUsage(),
    )

    collector.collect(item, result, workspace=None)

    log_path = tmp_path / "artifacts" / "demo-log" / "scorer_log.txt"
    assert log_path.exists()
    assert "FAILED tests.test_fix" in log_path.read_text(encoding="utf-8")


def test_artifact_collector_skips_empty_scorer_log(tmp_path) -> None:
    collector = ArtifactCollector(tmp_path)
    item = EvalItem(item_id="demo-nolog", dataset="swebench", intent="demo")
    result = EvalResult(
        item_id="demo-nolog",
        dataset="swebench",
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        passed=True,
        score=1.0,
        scorer_name="swebench_docker",
        scorer_log="",
        token_usage=TokenUsage(),
    )

    collector.collect(item, result, workspace=None)

    log_path = tmp_path / "artifacts" / "demo-nolog" / "scorer_log.txt"
    assert not log_path.exists()
