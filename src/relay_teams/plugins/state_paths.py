# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from relay_teams.paths import get_app_config_dir, get_project_root_or_none
from relay_teams.plugins.plugin_models import PluginScope

_PLUGINS_DIR_NAME = "plugins"
_PLUGINS_STATE_FILE_NAME = "plugins.json"
_PLUGINS_LOCAL_STATE_FILE_NAME = "plugins.local.json"
_MANAGED_PLUGINS_FILE_ENV_VAR = "RELAY_TEAMS_MANAGED_PLUGINS_FILE"


def get_plugin_user_state_file(*, app_config_dir: Path | None = None) -> Path:
    resolved_app_config = _resolve_app_config_dir(app_config_dir)
    return resolved_app_config / _PLUGINS_DIR_NAME / _PLUGINS_STATE_FILE_NAME


def get_plugin_project_state_file(
    *,
    app_config_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path | None:
    resolved_project_root = _resolve_project_root(project_root)
    if resolved_project_root is None:
        return None
    return (
        resolved_project_root
        / _active_config_dir_name(app_config_dir=app_config_dir)
        / _PLUGINS_STATE_FILE_NAME
    )


def get_plugin_project_local_state_file(
    *,
    app_config_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path | None:
    resolved_project_root = _resolve_project_root(project_root)
    if resolved_project_root is None:
        return None
    return (
        resolved_project_root
        / _active_config_dir_name(app_config_dir=app_config_dir)
        / _PLUGINS_LOCAL_STATE_FILE_NAME
    )


def get_plugin_managed_state_file() -> Path | None:
    raw_value = os.environ.get(_MANAGED_PLUGINS_FILE_ENV_VAR, "").strip()
    if not raw_value:
        return None
    return Path(raw_value).expanduser().resolve()


def get_plugin_state_file(
    *,
    scope: PluginScope,
    app_config_dir: Path | None = None,
    project_root: Path | None = None,
) -> Path | None:
    if scope == PluginScope.USER:
        return get_plugin_user_state_file(app_config_dir=app_config_dir)
    if scope == PluginScope.PROJECT:
        return get_plugin_project_state_file(
            app_config_dir=app_config_dir,
            project_root=project_root,
        )
    if scope == PluginScope.PROJECT_LOCAL:
        return get_plugin_project_local_state_file(
            app_config_dir=app_config_dir,
            project_root=project_root,
        )
    if scope == PluginScope.MANAGED:
        return get_plugin_managed_state_file()
    return None


def get_plugin_data_root(*, app_config_dir: Path | None = None) -> Path:
    resolved_app_config = _resolve_app_config_dir(app_config_dir)
    return resolved_app_config / _PLUGINS_DIR_NAME / "data"


def get_plugin_installed_root(*, app_config_dir: Path | None = None) -> Path:
    resolved_app_config = _resolve_app_config_dir(app_config_dir)
    return resolved_app_config / _PLUGINS_DIR_NAME / "installed"


def get_plugin_cache_root(*, app_config_dir: Path | None = None) -> Path:
    resolved_app_config = _resolve_app_config_dir(app_config_dir)
    return resolved_app_config / _PLUGINS_DIR_NAME / "cache"


def get_installed_plugin_version_dir(
    *,
    plugin_name: str,
    version: str,
    app_config_dir: Path | None = None,
) -> Path:
    return (
        get_plugin_installed_root(app_config_dir=app_config_dir)
        / _safe_path_segment(plugin_name, field_name="plugin_name")
        / _safe_path_segment(version, field_name="version")
    )


def _resolve_app_config_dir(app_config_dir: Path | None) -> Path:
    if app_config_dir is None:
        return get_app_config_dir().expanduser().resolve()
    return app_config_dir.expanduser().resolve()


def _resolve_project_root(project_root: Path | None) -> Path | None:
    if project_root is not None:
        return project_root.expanduser().resolve()
    return get_project_root_or_none(start_dir=Path.cwd())


def _active_config_dir_name(*, app_config_dir: Path | None) -> str:
    name = _resolve_app_config_dir(app_config_dir).name.strip()
    if not name:
        return ".relay-teams"
    return name


def _safe_path_segment(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if normalized in {".", ".."} or Path(normalized).name != normalized:
        raise ValueError(f"{field_name} must be a safe path segment")
    return normalized
