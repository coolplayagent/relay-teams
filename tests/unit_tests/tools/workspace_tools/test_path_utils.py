# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.tools.workspace_tools.path_utils import (
    resolve_workspace_path,
    resolve_workspace_tmp_path,
)
from agent_teams.workspace import WorkspaceHandle
from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def test_resolve_workspace_path_returns_path_within_workspace(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "src" / "app.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("print('ok')", encoding="utf-8")

    resolved = resolve_workspace_path(tmp_path, "src/app.py")

    assert resolved == nested.resolve()


def test_resolve_workspace_path_allows_workspace_root(tmp_path: Path) -> None:
    resolved = resolve_workspace_path(tmp_path, ".")

    assert resolved == tmp_path.resolve()


def test_resolve_workspace_path_rejects_escape_outside_workspace(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Path is outside workspace"):
        resolve_workspace_path(tmp_path, "../outside.txt")


def _build_workspace_handle(tmp_path: Path) -> WorkspaceHandle:
    profile = default_workspace_profile()
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            profile=profile,
        ),
        profile=profile,
        locations=WorkspaceLocations(
            workspace_dir=tmp_path,
            execution_root=tmp_path,
            readable_roots=(tmp_path,),
            writable_roots=(tmp_path,),
        ),
    )


def test_resolve_workspace_tmp_path_returns_path_within_tmp(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path)

    resolved = resolve_workspace_tmp_path(workspace, "reports/spec.md")

    assert resolved == (tmp_path / "tmp" / "reports" / "spec.md").resolve()


def test_resolve_workspace_tmp_path_rejects_escape_from_tmp(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path)

    with pytest.raises(ValueError, match="outside workspace tmp directory"):
        resolve_workspace_tmp_path(workspace, "../outside.txt")


def test_resolve_workspace_tmp_path_rejects_absolute_paths(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path)

    with pytest.raises(ValueError, match="relative to the workspace tmp directory"):
        resolve_workspace_tmp_path(workspace, str((tmp_path / "tmp" / "file.txt").resolve()))
