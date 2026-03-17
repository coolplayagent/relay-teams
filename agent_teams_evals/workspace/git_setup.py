from __future__ import annotations

import shutil
import subprocess
import uuid

from agent_teams_evals.config import EvalConfig
from agent_teams_evals.models import EvalItem
from agent_teams_evals.workspace.base import PreparedWorkspace, WorkspaceSetup


class GitWorkspaceSetup(WorkspaceSetup):
    def __init__(self, config: EvalConfig) -> None:
        self._config = config

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        if item.repo_url is None:
            raise ValueError(f"Item {item.item_id} has no repo_url")
        if item.base_commit is None:
            raise ValueError(f"Item {item.item_id} has no base_commit")

        item_dir = self._config.evals_workdir / item.item_id

        # Remove any directories left by previous partial runs.
        if item_dir.exists():
            for stale in item_dir.iterdir():
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)

        run_hash = uuid.uuid4().hex[:8]
        repo_path = item_dir / run_hash / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)

        timeout = self._config.git_clone_timeout_seconds
        subprocess.run(
            ["git", "clone", item.repo_url, str(repo_path)],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        subprocess.run(
            ["git", "checkout", item.base_commit],
            cwd=repo_path,
            check=True,
            capture_output=True,
            timeout=60.0,
        )

        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=repo_path,
            base_commit=item.base_commit,
        )

    def cleanup(self, workspace: PreparedWorkspace) -> None:
        # Delete the run-hash directory (parent of repo/).
        run_dir = workspace.repo_path.parent
        if run_dir.exists():
            shutil.rmtree(run_dir)
