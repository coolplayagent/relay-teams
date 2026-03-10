# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.paths import get_project_root_or_none


def get_frontend_dist_dir() -> Path:
    candidates = (
        _git_frontend_dist_dir(),
        _package_frontend_dist_dir(),
        _cwd_frontend_dist_dir(),
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _git_frontend_dist_dir() -> Path:
    project_root = get_project_root_or_none() or Path.cwd().resolve()
    return project_root / "frontend" / "dist"


def _package_frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "frontend" / "dist"


def _cwd_frontend_dist_dir() -> Path:
    return Path.cwd().resolve() / "frontend" / "dist"
