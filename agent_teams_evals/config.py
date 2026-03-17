from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = "http://127.0.0.1:8000"
    workspace_id: str = "default"
    execution_mode: str = "ai"
    run_timeout_seconds: float = 300.0
    output_dir: Path = Path(".agent_teams/evals/results")
    evals_workdir: Path = Path(".agent_teams/evals/workspaces")
    dataset_path: Path | None = None
    limit: int | None = None
    item_ids: tuple[str, ...] = ()
    concurrency: int = 1
    keep_workspaces: bool = False
    swebench_pass_threshold: float = 0.8
    git_clone_timeout_seconds: float = 120.0
