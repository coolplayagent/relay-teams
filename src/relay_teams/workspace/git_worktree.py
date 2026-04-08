# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from relay_teams.logger import get_logger, log_event
from relay_teams.paths import make_dirs

_GIT_TIMEOUT_SECONDS = 30.0
_logger = get_logger(__name__)


class GitWorktreeClient:
    def ensure_repository(self, repository_root: Path) -> Path:
        resolved_root = repository_root.expanduser().resolve()
        completed = self._run_git(
            ("rev-parse", "--show-toplevel"),
            cwd=resolved_root,
        )
        reported_root = Path(completed.stdout.strip()).expanduser().resolve()
        return reported_root

    def current_head(self, repository_root: Path) -> str:
        completed = self._run_git(
            ("rev-parse", "HEAD"),
            cwd=repository_root,
        )
        return completed.stdout.strip()

    def fetch_ref(
        self,
        repository_root: Path,
        *,
        remote: str = "origin",
        ref: str = "main",
    ) -> None:
        _ = self._run_git(
            ("fetch", remote, ref, "--quiet"),
            cwd=repository_root,
        )

    def resolve_ref(self, repository_root: Path, ref_name: str) -> str:
        completed = self._run_git(
            ("rev-parse", ref_name),
            cwd=repository_root,
        )
        return completed.stdout.strip()

    def add_worktree(
        self,
        *,
        repository_root: Path,
        branch_name: str,
        target_path: Path,
        start_point: str,
    ) -> None:
        make_dirs(target_path.parent, exist_ok=True)
        _ = self._run_git(
            (
                "worktree",
                "add",
                "--force",
                "-b",
                branch_name,
                str(target_path),
                start_point,
            ),
            cwd=repository_root,
        )
        log_event(
            _logger,
            20,
            event="workspace.git_worktree.added",
            message="Created git worktree",
            payload={
                "repository_root": str(repository_root),
                "branch_name": branch_name,
                "target_path": str(target_path),
            },
        )

    def remove_worktree(self, *, repository_root: Path, target_path: Path) -> None:
        _ = self._run_git(
            ("worktree", "remove", "--force", str(target_path)),
            cwd=repository_root,
        )
        log_event(
            _logger,
            20,
            event="workspace.git_worktree.removed",
            message="Removed git worktree",
            payload={
                "repository_root": str(repository_root),
                "target_path": str(target_path),
            },
        )

    def prune(self, repository_root: Path) -> None:
        _ = self._run_git(("worktree", "prune"), cwd=repository_root)

    def _run_git(
        self,
        args: tuple[str, ...],
        *,
        cwd: Path,
    ) -> subprocess.CompletedProcess[str]:
        git_binary = shutil.which("git")
        if git_binary is None:
            raise ValueError("Git executable is not available")

        command = [git_binary, *args]
        try:
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                check=False,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise ValueError(f"Failed to execute git: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Git command timed out") from exc

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            stdout = completed.stdout.strip()
            detail = stderr or stdout or "unknown git error"
            raise ValueError(f"Git command failed: {detail}")
        return completed
