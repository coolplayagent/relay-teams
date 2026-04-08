from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams_evals.backends.agent_teams import AgentTeamsBackend, AgentTeamsConfig
from relay_teams_evals.loaders.swebench_loader import SWEBenchLoader
from relay_teams_evals.models import EvalItem
from relay_teams_evals.runner import EvalRunner
from relay_teams_evals.scorers.swebench_scorer import SWEBenchScorer
from relay_teams_evals.workspace.git_setup import GitWorkspaceSetup
from relay_teams_evals.workspace.patch_extractor import PatchExtractor

_DATASET_PATH = Path(".agent_teams/evals/datasets/swebench-lite.jsonl")
_EVALS_WORKDIR = Path(".agent_teams/evals/workspaces")


def _load_items() -> list[EvalItem]:
    if not _DATASET_PATH.exists():
        return []
    return SWEBenchLoader().load(_DATASET_PATH)


@pytest.mark.parametrize("item", _load_items(), ids=lambda it: it.item_id)
def test_item(item: EvalItem, backend_url: str) -> None:
    backend = AgentTeamsBackend(AgentTeamsConfig(base_url=backend_url))
    workspace_setup = GitWorkspaceSetup(_EVALS_WORKDIR)
    patch_extractor = PatchExtractor()
    runner = EvalRunner(
        backend=backend,
        scorer=SWEBenchScorer(),
        workspace_setup=workspace_setup,
        patch_extractor=patch_extractor,
    )
    result = runner.run_item(item)
    assert result.passed, f"{item.item_id}: {result.scorer_detail}"
