# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workspace import WorkspaceHandle

_TMP_PATTERN_PREFIXES = ("tmp/", "tmp\\")


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
    return workspace.resolve_tmp_path(relative_path, write=True)


def resolve_workspace_glob_scope(
    workspace: WorkspaceHandle,
    pattern: str,
) -> tuple[Path, str, str | None]:
    if pattern == "tmp" or pattern.startswith(_TMP_PATTERN_PREFIXES):
        relative_pattern = pattern.removeprefix("tmp").lstrip("/\\")
        return (
            workspace.resolve_path("tmp", write=False),
            relative_pattern or "**",
            "tmp",
        )
    return workspace.resolve_path(".", write=False), pattern, None
