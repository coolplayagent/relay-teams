# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def _build_workspace_handle(
    *,
    workspace_dir: Path,
    scope_root: Path,
    execution_root: Path | None = None,
    readable_roots: tuple[Path, ...] | None = None,
    writable_roots: tuple[Path, ...] | None = None,
) -> WorkspaceHandle:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    scope_root.mkdir(parents=True, exist_ok=True)
    resolved_execution_root = execution_root or scope_root
    resolved_execution_root.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_dir / "tmp"
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
            workspace_dir=workspace_dir,
            scope_root=scope_root,
            execution_root=resolved_execution_root,
            tmp_root=tmp_root,
            readable_roots=readable_roots or (scope_root, tmp_root),
            writable_roots=writable_roots or (scope_root, tmp_root),
        ),
    )


def test_resolve_read_path_allows_absolute_path_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    external_file = tmp_path / "external" / "notes.txt"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_read_path(str(external_file))

    assert resolved == external_file.resolve()


def test_resolve_read_path_allows_absolute_path_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    workspace_file = workspace_root / "notes.txt"
    workspace_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_read_path(str(workspace_file.resolve()))

    assert resolved == workspace_file.resolve()


def test_resolve_read_path_allows_relative_escape_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    external_file = tmp_path / "outside.txt"
    external_file.write_text("outside\n", encoding="utf-8")

    resolved = workspace.resolve_read_path("../outside.txt")

    assert resolved == external_file.resolve()


def test_resolve_workdir_rejects_outside_path(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )

    with pytest.raises(ValueError, match="outside workspace write scope"):
        workspace.resolve_workdir("../outside")


def test_resolve_path_rejects_write_outside_workspace_with_allowed_roots(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )

    with pytest.raises(ValueError) as exc_info:
        workspace.resolve_path("../outside.txt", write=True)

    message = str(exc_info.value)
    assert "outside workspace write scope" in message
    assert "requested=../outside.txt" in message
    assert f"resolved={(tmp_path / 'outside.txt').resolve()}" in message
    assert str(workspace_root.resolve()) in message
    assert str((workspace_root / "tmp").resolve()) in message


def test_resolve_path_routes_tmp_prefix_to_managed_tmp_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
    )

    resolved = workspace.resolve_path("tmp/reports/spec.md", write=True)

    assert resolved == (workspace_dir / "tmp" / "reports" / "spec.md").resolve()


def test_resolve_path_keeps_dot_tmp_inside_execution_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    execution_root = scope_root / "app"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
        execution_root=execution_root,
    )

    resolved = workspace.resolve_path("./tmp/spec.md", write=True)

    assert resolved == (execution_root / "tmp" / "spec.md").resolve()


def test_resolve_path_allows_parent_navigation_within_scope_root(
    tmp_path: Path,
) -> None:
    scope_root = tmp_path / "project"
    execution_root = scope_root / "app" / "backend"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
        execution_root=execution_root,
    )

    resolved = workspace.resolve_path("../README.md", write=False)

    assert resolved == (scope_root / "app" / "README.md").resolve()


def test_resolve_workdir_routes_tmp_prefix_to_managed_tmp_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
    )

    assert workspace.resolve_workdir("tmp") == (workspace_dir / "tmp").resolve()
