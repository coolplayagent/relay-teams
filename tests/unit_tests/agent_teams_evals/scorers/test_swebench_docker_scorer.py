from __future__ import annotations

from pathlib import Path

from agent_teams_evals.models import EvalItem, RunOutcome, TokenUsage
from agent_teams_evals.scorers import swebench_docker_scorer
from agent_teams_evals.scorers.swebench_docker_scorer import SWEBenchDockerScorer
from agent_teams_evals.workspace.base import PreparedWorkspace


def test_swebench_docker_scorer_records_patch_jaccard_auxiliary_score(
    monkeypatch,
) -> None:
    monkeypatch.setattr(swebench_docker_scorer, "_run_pytest", lambda *_args, **_kwargs: True)

    item = EvalItem(
        item_id="demo",
        dataset="swebench",
        intent="demo",
        reference_patch=(
            "diff --git a/pkg.py b/pkg.py\n"
            "@@ -1 +1 @@\n"
            "-old_value\n"
            "+new_value\n"
        ),
        fail_to_pass=("tests.test_fix",),
        pass_to_pass=("tests.test_keep",),
    )
    workspace = PreparedWorkspace(
        item_id="demo",
        repo_path=Path("."),
        base_commit="abc123",
        container_id="container-1",
        container_repo_path="/testbed",
    )

    result = SWEBenchDockerScorer().score(
        item=item,
        run_id="run-1",
        session_id="session-1",
        outcome=RunOutcome.COMPLETED,
        agent_output="",
        generated_patch=(
            "diff --git a/pkg.py b/pkg.py\n"
            "@@ -1 +1 @@\n"
            "-old_value\n"
            "+new_value\n"
        ),
        token_usage=TokenUsage(),
        duration_seconds=1.0,
        workspace=workspace,
        error=None,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.auxiliary_scores["patch_jaccard"].score == 1.0
    assert "aux.patch_jaccard=1.000" in result.scorer_detail
