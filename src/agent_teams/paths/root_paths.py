# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess


_GIT_TOPLEVEL_CMD: tuple[str, str, str] = ("git", "rev-parse", "--show-toplevel")
_GIT_TIMEOUT_SECONDS = 5.0


def get_user_home_dir() -> Path:
    return Path.home().resolve()


def get_project_root(start_dir: Path | None = None) -> Path:
    project_root = get_project_root_or_none(start_dir=start_dir)
    if project_root is None:
        return Path.cwd().resolve()
    return project_root


def get_project_root_or_none(start_dir: Path | None = None) -> Path | None:
    command_cwd = _resolve_start_dir(start_dir)
    try:
        completed = subprocess.run(
            list(_GIT_TOPLEVEL_CMD),
            cwd=str(command_cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except OSError:
        return Path.cwd().resolve()
    except subprocess.TimeoutExpired:
        return Path.cwd().resolve()

    if completed.returncode != 0:
        return Path.cwd().resolve()

    raw_stdout = completed.stdout.strip()
    if not raw_stdout:
        return Path.cwd().resolve()
    return Path(raw_stdout).expanduser().resolve()


def _resolve_start_dir(start_dir: Path | None) -> Path:
    if start_dir is None:
        return Path.cwd().resolve()

    resolved = start_dir.expanduser().resolve()
    if resolved.is_file():
        return resolved.parent
    return resolved
