# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import shutil

from relay_teams.paths import get_project_root_or_none


def get_builtin_root() -> Path:
    return Path(__file__).resolve().parent


def get_builtin_roles_dir() -> Path:
    return _resolve_builtin_dir("roles", "*.md")


def get_builtin_skills_dir() -> Path:
    return _resolve_builtin_dir("skills", "*/SKILL.md")


def get_builtin_logger_ini_path() -> Path:
    return get_builtin_root() / "logging" / "logger.ini"


def get_builtin_model_config_path() -> Path:
    return get_builtin_root() / "config" / "model.json"


def get_builtin_notifications_config_path() -> Path:
    return get_builtin_root() / "config" / "notifications.json"


def get_builtin_orchestration_config_path() -> Path:
    return get_builtin_root() / "config" / "orchestration.json"


def get_builtin_prompts_config_path() -> Path:
    return get_builtin_root() / "config" / "prompts.json"


def ensure_app_config_bootstrap(config_dir: Path) -> None:
    resolved_config_dir = config_dir.expanduser().resolve()
    resolved_config_dir.mkdir(parents=True, exist_ok=True)
    (resolved_config_dir / "log").mkdir(parents=True, exist_ok=True)
    (resolved_config_dir / "roles").mkdir(parents=True, exist_ok=True)
    (resolved_config_dir / "skills").mkdir(parents=True, exist_ok=True)

    copy_builtin_file_if_missing(
        source_path=get_builtin_logger_ini_path(),
        target_path=resolved_config_dir / "logger.ini",
    )
    copy_builtin_file_if_missing(
        source_path=get_builtin_model_config_path(),
        target_path=resolved_config_dir / "model.json",
    )
    copy_builtin_file_if_missing(
        source_path=get_builtin_notifications_config_path(),
        target_path=resolved_config_dir / "notifications.json",
    )
    copy_builtin_file_if_missing(
        source_path=get_builtin_orchestration_config_path(),
        target_path=resolved_config_dir / "orchestration.json",
    )
    copy_builtin_file_if_missing(
        source_path=get_builtin_prompts_config_path(),
        target_path=resolved_config_dir / "prompts.json",
    )


def copy_builtin_file_if_missing(*, source_path: Path, target_path: Path) -> None:
    resolved_target_path = target_path.expanduser().resolve()
    if resolved_target_path.exists():
        return
    resolved_target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, resolved_target_path)


def _resolve_builtin_dir(directory_name: str, expected_pattern: str) -> Path:
    builtin_dir = get_builtin_root() / directory_name
    if any(builtin_dir.glob(expected_pattern)):
        return builtin_dir

    project_root = get_project_root_or_none(start_dir=Path.cwd())
    if project_root is None:
        return builtin_dir

    fallback_dir = project_root / "src" / "relay_teams" / "builtin" / directory_name
    if any(fallback_dir.glob(expected_pattern)):
        return fallback_dir
    return builtin_dir
