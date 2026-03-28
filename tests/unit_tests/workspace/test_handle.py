# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.workspace import WorkspaceHandle
from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def _build_workspace_handle(root_path: Path) -> WorkspaceHandle:
    root_path.mkdir(parents=True, exist_ok=True)
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
            workspace_dir=root_path,
            execution_root=root_path,
            readable_roots=(root_path,),
            writable_roots=(root_path,),
        ),
    )


def test_resolve_read_path_allows_absolute_path_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    external_file = tmp_path / "external" / "notes.txt"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_read_path(str(external_file))

    assert resolved == external_file.resolve()


def test_resolve_read_path_allows_relative_escape_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(workspace_root)
    external_file = tmp_path / "outside.txt"
    external_file.write_text("outside\n", encoding="utf-8")

    resolved = workspace.resolve_read_path("../outside.txt")

    assert resolved == external_file.resolve()


def test_resolve_workdir_keeps_workspace_boundary_for_outside_path(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")

    with pytest.raises(ValueError, match="outside workspace read scope"):
        workspace.resolve_workdir("../outside")
