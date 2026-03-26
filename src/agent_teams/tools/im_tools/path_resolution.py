# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from agent_teams.workspace import WorkspaceHandle


def resolve_im_file_path(*, file_path: str, workspace: WorkspaceHandle) -> Path:
    normalized = _normalize_input_path(file_path)
    try:
        workspace_resolved = workspace.resolve_path(normalized, write=False)
        if workspace_resolved.exists():
            return workspace_resolved
    except ValueError:
        pass

    candidate = Path(normalized)
    if candidate.is_absolute():
        return candidate.resolve()

    cwd_candidate = (Path.cwd() / candidate).resolve()
    if cwd_candidate.exists():
        return cwd_candidate

    workspace_candidate = (workspace.root_path / candidate).resolve()
    if workspace_candidate.exists():
        return workspace_candidate

    return cwd_candidate


def _normalize_input_path(raw_path: str) -> str:
    normalized = raw_path.strip().strip('"').strip("'")
    if not normalized:
        raise ValueError("file_path cannot be empty.")
    normalized = os.path.expandvars(os.path.expanduser(normalized))
    if sys.platform == "win32" and normalized.startswith("/"):
        match = re.match(r"^/([a-zA-Z])/(.*)", normalized)
        if match is not None:
            normalized = f"{match.group(1)}:/{match.group(2)}"
    return normalized
