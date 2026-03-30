# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.tools.im_tools.path_resolution import resolve_im_file_path
from agent_teams.workspace import WorkspaceHandle
from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceRef,
    default_workspace_profile,
)


def test_resolve_im_file_path_prefers_workspace_scope(tmp_path: Path) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    file_path = workspace.root_path / "reports" / "brief.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("brief", encoding="utf-8")

    resolved = resolve_im_file_path(
        file_path="reports/brief.txt",
        workspace=workspace,
    )

    assert resolved == file_path.resolve()


def test_resolve_im_file_path_allows_absolute_path_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    external_file = tmp_path / "external" / "report.pdf"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("report", encoding="utf-8")

    resolved = resolve_im_file_path(
        file_path=str(external_file),
        workspace=workspace,
    )

    assert resolved == external_file.resolve()


def test_resolve_im_file_path_allows_cwd_relative_path_outside_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    current_dir = tmp_path / "current"
    current_dir.mkdir(parents=True)
    external_file = current_dir / "assets" / "summary.txt"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("summary", encoding="utf-8")
    monkeypatch.chdir(current_dir)

    resolved = resolve_im_file_path(
        file_path="assets/summary.txt",
        workspace=workspace,
    )

    assert resolved == external_file.resolve()


def test_resolve_im_file_path_expands_environment_variables_and_quotes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    external_dir = tmp_path / "external"
    external_dir.mkdir(parents=True)
    external_file = external_dir / "notes.txt"
    external_file.write_text("notes", encoding="utf-8")
    monkeypatch.setenv("IM_TEST_FILE", str(external_file))

    resolved = resolve_im_file_path(
        file_path='"%IM_TEST_FILE%"',
        workspace=workspace,
    )

    assert resolved == external_file.resolve()


def test_resolve_im_file_path_expands_percent_variables_case_insensitively(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = _build_workspace_handle(tmp_path / "workspace")
    external_dir = tmp_path / "external"
    external_dir.mkdir(parents=True)
    external_file = external_dir / "case-insensitive.txt"
    external_file.write_text("notes", encoding="utf-8")
    monkeypatch.setenv("IM_TEST_FILE_MIXED", str(external_file))

    resolved = resolve_im_file_path(
        file_path='"%im_test_file_mixed%"',
        workspace=workspace,
    )

    assert resolved == external_file.resolve()


def _build_workspace_handle(root_path: Path) -> WorkspaceHandle:
    root_path.mkdir(parents=True, exist_ok=True)
    profile = default_workspace_profile()
    tmp_root = root_path / ".tmp"
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
            scope_root=root_path,
            execution_root=root_path,
            tmp_root=tmp_root,
            readable_roots=(root_path, tmp_root),
            writable_roots=(root_path, tmp_root),
        ),
    )
