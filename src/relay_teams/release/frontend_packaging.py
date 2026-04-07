# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import shutil


_FRONTEND_DIST_RELATIVE_PATH = Path("frontend") / "dist"
_PACKAGE_FRONTEND_DIST_RELATIVE_PATH = Path("relay_teams") / "frontend" / "dist"


def get_frontend_dist_source_dir(project_root: Path) -> Path:
    return project_root / _FRONTEND_DIST_RELATIVE_PATH


def get_frontend_package_dist_dir(build_lib: Path) -> Path:
    return build_lib / _PACKAGE_FRONTEND_DIST_RELATIVE_PATH


def copy_frontend_dist(*, project_root: Path, build_lib: Path) -> Path:
    source_dir = get_frontend_dist_source_dir(project_root)
    if not source_dir.is_dir():
        raise FileNotFoundError(
            f"Frontend build artifacts were not found at {source_dir}"
        )

    target_dir = get_frontend_package_dist_dir(build_lib)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, target_dir)
    return target_dir
