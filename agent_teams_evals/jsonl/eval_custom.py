from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams_evals.config import EvalConfig
from agent_teams_evals.loaders.jsonl_loader import JsonlLoader
from agent_teams_evals.models import EvalItem
from agent_teams_evals.runner import EvalRunner
from agent_teams_evals.scorers.keyword_scorer import KeywordScorer

_DATASET_PATH = Path(".agent_teams/evals/datasets/custom.jsonl")


def _load_items() -> list[EvalItem]:
    if not _DATASET_PATH.exists():
        return []
    return JsonlLoader().load(_DATASET_PATH)


@pytest.mark.parametrize("item", _load_items(), ids=lambda it: it.item_id)
def test_item(item: EvalItem, backend_url: str) -> None:
    config = EvalConfig(
        base_url=backend_url,
        output_dir=Path(".agent_teams/evals/results/jsonl"),
    )
    runner = EvalRunner(config=config, scorer=KeywordScorer())
    result = runner.run_item(item)
    assert result.passed, f"{item.item_id}: {result.scorer_detail}"
