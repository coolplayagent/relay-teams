# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

from agent_teams.paths import get_project_root as resolve_project_root

CONFIG_DIR_ENV_VAR = "AGENT_TEAMS_CONFIG_DIR"


def get_project_root() -> Path:
    return resolve_project_root()


def get_config_dir() -> Path:
    raw_override = os.environ.get(CONFIG_DIR_ENV_VAR, "").strip()
    if not raw_override:
        return get_project_root() / ".agent_teams"
    return Path(raw_override).expanduser().resolve()


def get_frontend_dist_dir() -> Path:
    return get_project_root() / "frontend" / "dist"
