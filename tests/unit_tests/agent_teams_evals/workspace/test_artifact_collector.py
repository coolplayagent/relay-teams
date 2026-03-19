from __future__ import annotations

import json

from agent_teams_evals.models import AuxiliaryScore, EvalItem, EvalResult, RunOutcome, TokenUsage
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
