# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess


_APP_CONFIG_DIR_NAME = ".agent-teams"
_GIT_TOPLEVEL_CMD: tuple[str, str, str] = ("git", "rev-parse", "--show-toplevel")
_GIT_TIMEOUT_SECONDS = 5.0


def get_user_home_dir() -> Path:
    return Path.home().resolve()


def get_app_config_dir(user_home_dir: Path | None = None) -> Path:
    resolved_home_dir = (
        get_user_home_dir()
        if user_home_dir is None
        else user_home_dir.expanduser().resolve()
    )
    return resolved_home_dir / _APP_CONFIG_DIR_NAME


def get_user_config_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir)


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
        return None
    except subprocess.TimeoutExpired:
        return None

    if completed.returncode != 0:
        return None

    raw_stdout = completed.stdout.strip()
    if not raw_stdout:
        return None
    return Path(raw_stdout).expanduser().resolve()


def get_project_config_dir(project_root: Path | None = None) -> Path:
    _ = project_root
    return get_app_config_dir()


def get_project_log_dir(project_root: Path | None = None) -> Path:
    _ = project_root
    return get_app_config_dir() / "log"


def _resolve_project_root_from_context() -> Path:
    cwd = Path.cwd().resolve()
    return get_project_root_or_none(start_dir=cwd) or cwd


def _resolve_start_dir(start_dir: Path | None) -> Path:
    if start_dir is None:
        return Path.cwd().resolve()

    resolved = start_dir.expanduser().resolve()
    if resolved.is_file():
        return resolved.parent
    return resolved
