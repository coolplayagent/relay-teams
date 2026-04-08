# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path
import subprocess


_APP_CONFIG_DIR_ENV_VAR = "RELAY_TEAMS_CONFIG_DIR"
_APP_CONFIG_DIR_NAME = ".relay-teams"
_GIT_TOPLEVEL_CMD: tuple[str, str, str] = ("git", "rev-parse", "--show-toplevel")
_GIT_TIMEOUT_SECONDS = 5.0


def get_user_home_dir() -> Path:
    return Path.home().resolve()


def get_app_config_dir(user_home_dir: Path | None = None) -> Path:
    env_config_dir = get_app_config_dir_override()
    if env_config_dir is not None:
        return env_config_dir

    resolved_home_dir = (
        get_user_home_dir()
        if user_home_dir is None
        else user_home_dir.expanduser().resolve()
    )
    return resolved_home_dir / _APP_CONFIG_DIR_NAME


def get_user_config_dir(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir)


def get_app_config_dir_override() -> Path | None:
    return _resolve_optional_path_from_env(_APP_CONFIG_DIR_ENV_VAR)


def get_app_bin_dir() -> Path:
    return get_app_config_dir() / "bin"


def get_app_config_file_path(
    file_name: str | Path,
    *,
    config_dir: Path | None = None,
) -> Path:
    base_dir = (
        get_app_config_dir()
        if config_dir is None
        else config_dir.expanduser().resolve()
    )
    return base_dir / Path(file_name)


def format_app_config_file_reference(
    file_name: str | Path,
    *,
    config_dir: Path | None = None,
) -> str:
    target_path = get_app_config_file_path(file_name, config_dir=config_dir)
    return f'"{target_path}"'


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


def _resolve_optional_path_from_env(env_key: str) -> Path | None:
    raw_value = _get_non_empty_env_value(env_key)
    if raw_value is None:
        return None

    return Path(raw_value).expanduser().resolve()


def _get_non_empty_env_value(env_key: str) -> str | None:
    raw_value = os.environ.get(env_key)
    if raw_value is None:
        return None

    normalized_value = raw_value.strip()
    if not normalized_value:
        return None

    return normalized_value
