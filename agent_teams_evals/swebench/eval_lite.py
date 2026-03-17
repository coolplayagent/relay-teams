from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams_evals.config import EvalConfig
from agent_teams_evals.loaders.swebench_loader import SWEBenchLoader
from agent_teams_evals.models import EvalItem
from agent_teams_evals.runner import EvalRunner
from agent_teams_evals.scorers.swebench_scorer import SWEBenchScorer
from agent_teams_evals.workspace.git_setup import GitWorkspaceSetup
from agent_teams_evals.workspace.patch_extractor import PatchExtractor

_DATASET_PATH = Path(".agent_teams/evals/datasets/swebench-lite.jsonl")


def _load_items() -> list[EvalItem]:
    if not _DATASET_PATH.exists():
        return []
    return SWEBenchLoader().load(_DATASET_PATH)


@pytest.mark.parametrize("item", _load_items(), ids=lambda it: it.item_id)
def test_item(item: EvalItem, backend_url: str) -> None:
    config = EvalConfig(
        base_url=backend_url,
        output_dir=Path(".agent_teams/evals/results/swebench"),
    )
    scorer = SWEBenchScorer(config)
    workspace_setup = GitWorkspaceSetup(config)
    patch_extractor = PatchExtractor()

    runner = EvalRunner(
        config=config,
        scorer=scorer,
        workspace_setup=workspace_setup,
        patch_extractor=patch_extractor,
    )
    result = runner.run_item(item)
    assert result.passed, f"{item.item_id}: {result.scorer_detail}"
