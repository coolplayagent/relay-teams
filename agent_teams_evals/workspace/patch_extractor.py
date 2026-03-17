from __future__ import annotations

import subprocess

from agent_teams_evals.workspace.base import PreparedWorkspace


class PatchExtractor:
    def extract(self, workspace: PreparedWorkspace) -> str:
        result = subprocess.run(
            ["git", "diff"],
            cwd=workspace.repo_path,
            capture_output=True,
            text=True,
        )
        return result.stdout
