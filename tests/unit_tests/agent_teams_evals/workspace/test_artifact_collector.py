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
