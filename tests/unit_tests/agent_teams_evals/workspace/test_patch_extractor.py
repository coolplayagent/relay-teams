from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_teams_evals.workspace.base import PreparedWorkspace
from agent_teams_evals.workspace.patch_extractor import PatchExtractor


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_patch_extractor_ignores_untracked_files(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    subprocess.run(
        ["git", "-C", str(repo_path), "init"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.email", "evals@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "config", "user.name", "Evals"],
        check=True,
        capture_output=True,
    )

    tracked_file = repo_path / "tracked.txt"
    tracked_file.write_text("before\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "tracked.txt"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )

    tracked_file.write_text("after\n", encoding="utf-8")
    (repo_path / "new_file.txt").write_text("untracked\n", encoding="utf-8")

    patch = PatchExtractor().extract(
        PreparedWorkspace(item_id="demo", repo_path=repo_path, base_commit="abc123")
    )

    assert "tracked.txt" in patch
    assert "new_file.txt" not in patch
