from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

from relay_teams_evals.models import EvalItem
from relay_teams_evals.workspace.base import PreparedWorkspace, WorkspaceSetup


class GitWorkspaceSetup(WorkspaceSetup):
    def __init__(
        self,
        evals_workdir: Path,
        git_clone_timeout_seconds: float = 120.0,
    ) -> None:
        self._evals_workdir = evals_workdir
        self._clone_timeout = git_clone_timeout_seconds

    def prepare(self, item: EvalItem) -> PreparedWorkspace:
        if item.repo_url is None:
            raise ValueError(f"Item {item.item_id} has no repo_url")
        if item.base_commit is None:
            raise ValueError(f"Item {item.item_id} has no base_commit")

        item_dir = self._evals_workdir / item.item_id

        # Remove any directories left by previous partial runs.
        if item_dir.exists():
            for stale in item_dir.iterdir():
                if stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)

        run_hash = uuid.uuid4().hex[:8]
        repo_path = item_dir / run_hash / "repo"
        repo_path.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                ["git", "clone", item.repo_url, str(repo_path)],
                check=True,
                capture_output=True,
                timeout=self._clone_timeout,
            )
            subprocess.run(
                ["git", "checkout", item.base_commit],
                cwd=repo_path,
                check=True,
                capture_output=True,
                timeout=self._clone_timeout,
            )
        except Exception:
            shutil.rmtree(repo_path.parent, ignore_errors=True)
            raise

        return PreparedWorkspace(
            item_id=item.item_id,
            repo_path=repo_path,
            base_commit=item.base_commit,
        )

    def cleanup(self, workspace: PreparedWorkspace) -> None:
        run_dir = workspace.repo_path.parent
        if run_dir.exists():
            shutil.rmtree(run_dir)
