# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import shutil


def get_builtin_root() -> Path:
    return Path(__file__).resolve().parent


def get_builtin_roles_dir() -> Path:
    return get_builtin_root() / "roles"


def get_builtin_skills_dir() -> Path:
    return get_builtin_root() / "skills"


def get_builtin_logger_ini_path() -> Path:
    return get_builtin_root() / "logging" / "logger.ini"


def get_builtin_model_config_path() -> Path:
    return get_builtin_root() / "config" / "model.json"


def get_builtin_notifications_config_path() -> Path:
    return get_builtin_root() / "config" / "notifications.json"


def get_builtin_orchestration_config_path() -> Path:
    return get_builtin_root() / "config" / "orchestration.json"


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


def copy_builtin_file_if_missing(*, source_path: Path, target_path: Path) -> None:
    resolved_target_path = target_path.expanduser().resolve()
    if resolved_target_path.exists():
        return
    resolved_target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, resolved_target_path)
