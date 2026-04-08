from __future__ import annotations

import subprocess

from relay_teams_evals.workspace.base import PreparedWorkspace


class PatchExtractor:
    def extract(self, workspace: PreparedWorkspace) -> str:
        if workspace.container_id:
            # Docker mode: the code lives inside the container at container_repo_path.
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    workspace.container_id,
                    "git",
                    "-C",
                    workspace.container_repo_path or "/testbed",
                    "diff",
                    "--",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        else:
            result = subprocess.run(
                ["git", "-C", str(workspace.repo_path), "diff", "--"],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
        return result.stdout or ""
