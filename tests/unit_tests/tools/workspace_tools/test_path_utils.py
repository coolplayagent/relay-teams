# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.tools.workspace_tools.path_utils import (
    resolve_workspace_glob_scope,
    resolve_workspace_path,
    resolve_workspace_tmp_path,
)
from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceMountProvider,
    WorkspaceRef,
    build_local_workspace_mount,
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
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            default_mount_name="default",
            mount_names=("default",),
        ),
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=scope_root,
                working_directory=(
                    resolved_execution_root.relative_to(scope_root).as_posix()
                    if resolved_execution_root != scope_root
                    else "."
                ),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="default",
            provider=WorkspaceMountProvider.LOCAL,
            scope_root=scope_root,
            execution_root=resolved_execution_root,
            tmp_root=tmp_root,
            readable_roots=readable_roots or (scope_root, tmp_root),
            writable_roots=writable_roots or (scope_root, tmp_root),
        ),
    )


def test_resolve_workspace_tmp_path_returns_path_within_tmp(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=tmp_path / "project",
    )

    resolved = resolve_workspace_tmp_path(workspace, "reports/spec.md")

    assert (
        resolved
        == (
            tmp_path
            / ".agent-teams"
            / "workspaces"
            / "workspace"
            / "tmp"
            / "reports"
            / "spec.md"
        ).resolve()
    )


def test_resolve_workspace_tmp_path_rejects_escape_from_tmp(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=tmp_path / "project",
    )

    with pytest.raises(ValueError, match="outside workspace tmp directory"):
        resolve_workspace_tmp_path(workspace, "../outside.txt")


def test_resolve_workspace_tmp_path_rejects_absolute_paths(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=tmp_path / "project",
    )

    with pytest.raises(ValueError, match="relative to the workspace tmp directory"):
        resolve_workspace_tmp_path(
            workspace,
            str(
                (
                    tmp_path
                    / ".agent-teams"
                    / "workspaces"
                    / "workspace"
                    / "tmp"
                    / "file.txt"
                ).resolve()
            ),
        )


def test_resolve_workspace_glob_scope_routes_tmp_prefix_to_tmp_root(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=tmp_path / "project",
    )

    root, pattern, logical_prefix = resolve_workspace_glob_scope(
        workspace,
        "tmp/**/*.log",
    )

    assert root == workspace.tmp_root.resolve()
    assert pattern == "**/*.log"
    assert logical_prefix == "tmp"


def test_resolve_workspace_glob_scope_keeps_execution_root_for_normal_patterns(
    tmp_path: Path,
) -> None:
    execution_root = tmp_path / "project" / "src"
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=tmp_path / "project",
        execution_root=execution_root,
    )

    root, pattern, logical_prefix = resolve_workspace_glob_scope(
        workspace,
        "**/*.py",
    )

    assert root == execution_root.resolve()
    assert pattern == "**/*.py"
    assert logical_prefix is None


def test_resolve_workspace_glob_scope_allows_execution_root_outside_read_scope(
    tmp_path: Path,
) -> None:
    scope_root = tmp_path / "project"
    execution_root = scope_root / "src"
    readable_root = scope_root / "docs"
    workspace = _build_workspace_handle(
        workspace_dir=tmp_path / ".agent-teams" / "workspaces" / "workspace",
        scope_root=scope_root,
        execution_root=execution_root,
        readable_roots=(
            readable_root,
            (tmp_path / ".agent-teams" / "workspaces" / "workspace" / "tmp"),
        ),
    )

    root, pattern, logical_prefix = resolve_workspace_glob_scope(
        workspace,
        "**/*.md",
    )

    assert root == execution_root.resolve()
    assert pattern == "**/*.md"
    assert logical_prefix is None
