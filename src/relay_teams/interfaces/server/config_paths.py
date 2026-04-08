# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib.util
from pathlib import Path

from relay_teams.paths import get_project_root_or_none


def get_frontend_dist_dir() -> Path:
    candidates = _frontend_dist_candidates()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _frontend_dist_candidates() -> tuple[Path, ...]:
    candidates: list[Path] = []
    git_frontend_dist_dir = _git_frontend_dist_dir()
    if git_frontend_dist_dir is not None:
        candidates.append(git_frontend_dist_dir)
    candidates.extend((_package_frontend_dist_dir(), _cwd_frontend_dist_dir()))
    return tuple(candidates)


def _git_frontend_dist_dir() -> Path | None:
    project_root = get_project_root_or_none()
    if project_root is None:
        return None
    return project_root / "frontend" / "dist"


def _package_frontend_dist_dir() -> Path:
    frontend_package_spec = importlib.util.find_spec("relay_teams.frontend")
    if frontend_package_spec is None or frontend_package_spec.origin is None:
        return Path(__file__).resolve().parents[2] / "frontend" / "dist"
    return Path(frontend_package_spec.origin).resolve().parent / "dist"


def _cwd_frontend_dist_dir() -> Path:
    return Path.cwd().resolve() / "frontend" / "dist"
