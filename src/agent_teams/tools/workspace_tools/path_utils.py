# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    candidate = (workspace_root / relative_path).resolve()
    root = workspace_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path is outside workspace: {relative_path}")
    return candidate
