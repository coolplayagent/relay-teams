# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workspace import WorkspaceHandle


def resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    candidate = (workspace_root / relative_path).resolve()
    root = workspace_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"Path is outside workspace: {relative_path}")
    return candidate


def resolve_workspace_tmp_path(
    workspace: WorkspaceHandle,
    relative_path: str,
) -> Path:
    requested_path = Path(relative_path)
    if requested_path.is_absolute():
        raise ValueError(
            f"Path must be relative to the workspace tmp directory: {relative_path}"
        )

    workspace_root = workspace.root_path.resolve()
    tmp_root = (workspace_root / "tmp").resolve()
    candidate = (tmp_root / requested_path).resolve()

    if candidate == tmp_root:
        raise ValueError("Path must point to a file inside the workspace tmp directory")
    if tmp_root not in candidate.parents:
        raise ValueError(f"Path is outside workspace tmp directory: {relative_path}")

    workspace_relative_path = candidate.relative_to(workspace_root).as_posix()
    return workspace.resolve_path(workspace_relative_path, write=True)
