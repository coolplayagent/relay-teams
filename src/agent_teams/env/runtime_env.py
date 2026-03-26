# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

from agent_teams.paths import get_app_config_dir
from agent_teams.secrets import get_secret_store, is_sensitive_env_key

_ENV_FILE_NAME = ".env"
_PROCESS_ENV_BASELINE: dict[str, str] = dict(os.environ)
_SYNCED_APP_ENV_KEYS: set[str] = set()
_APP_ENV_SECRET_NAMESPACE = "app_env"


def get_app_env_file_path(user_home_dir: Path | None = None) -> Path:
    return get_app_config_dir(user_home_dir=user_home_dir) / _ENV_FILE_NAME


def get_user_env_file_path(user_home_dir: Path | None = None) -> Path:
    return get_app_env_file_path(user_home_dir=user_home_dir)


def get_project_env_file_path(project_root: Path | None = None) -> Path:
    _ = project_root
    return get_app_env_file_path()


def load_env_file(env_file_path: Path) -> dict[str, str]:
    resolved_path = env_file_path.expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in resolved_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        values[normalized_key] = _strip_quotes(value.strip())
    return values


def load_merged_env_vars(
    *,
    project_root: Path | None = None,
    user_home_dir: Path | None = None,
    extra_env_files: tuple[Path, ...] = (),
    include_process_env: bool = True,
) -> dict[str, str]:
    merged: dict[str, str] = {}

    _ = project_root
    app_env_path = get_app_env_file_path(user_home_dir=user_home_dir)
    merged.update(load_env_file(app_env_path))
    merged.update(load_secret_env_vars(app_env_path.parent))

    for file_path in extra_env_files:
        merged.update(load_env_file(file_path))

    if include_process_env:
        merged.update(dict(os.environ))

    return merged


def sync_app_env_to_process_env(env_file_path: Path | None = None) -> dict[str, str]:
    resolved_env_file = (
        (get_app_env_file_path() if env_file_path is None else env_file_path)
        .expanduser()
        .resolve()
    )
    app_env = load_env_file(resolved_env_file)
    app_env.update(load_secret_env_vars(resolved_env_file.parent))
    managed_keys = _SYNCED_APP_ENV_KEYS | set(app_env.keys())
    for key in managed_keys:
        if key in app_env:
            os.environ[key] = app_env[key]
            continue
        baseline_value = _PROCESS_ENV_BASELINE.get(key)
        if baseline_value is None:
            os.environ.pop(key, None)
            continue
        os.environ[key] = baseline_value
    _SYNCED_APP_ENV_KEYS.clear()
    _SYNCED_APP_ENV_KEYS.update(app_env.keys())
    return app_env.copy()


def load_secret_env_vars(config_dir: Path) -> dict[str, str]:
    secret_store = get_secret_store()
    return {
        key: value
        for key, value in secret_store.get_owner_secrets(
            config_dir,
            namespace=_APP_ENV_SECRET_NAMESPACE,
            owner_id="app",
        ).items()
        if is_sensitive_env_key(key)
    }


def get_env_var(
    key: str,
    default: str | None = None,
    *,
    merged_env: Mapping[str, str] | None = None,
    project_root: Path | None = None,
    user_home_dir: Path | None = None,
    extra_env_files: tuple[Path, ...] = (),
    include_process_env: bool = True,
) -> str | None:
    if merged_env is None:
        resolved_env = load_merged_env_vars(
            project_root=project_root,
            user_home_dir=user_home_dir,
            extra_env_files=extra_env_files,
            include_process_env=include_process_env,
        )
    else:
        resolved_env = merged_env

    if key in resolved_env:
        return resolved_env[key]
    return default


def _strip_quotes(value: str) -> str:
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    return value
