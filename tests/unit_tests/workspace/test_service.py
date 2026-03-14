# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from agent_teams.workspace import WorkspaceRepository, WorkspaceService


def test_workspace_service_creates_and_lists_workspace(tmp_path: Path) -> None:
    db_path = tmp_path / "workspace_service.db"
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(repository=WorkspaceRepository(db_path))

    created = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    assert created.workspace_id == "project-alpha"
    assert created.root_path == root_path.resolve()
    listed = service.list_workspaces()
    assert len(listed) == 1
    assert listed[0].workspace_id == "project-alpha"


def test_workspace_service_rejects_missing_root(tmp_path: Path) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )

    with pytest.raises(ValueError, match="does not exist"):
        _ = service.create_workspace(
            workspace_id="missing",
            root_path=tmp_path / "missing-root",
        )


def test_workspace_service_create_for_root_reuses_existing_workspace(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "Project Root"
    root_path.mkdir()

    created = service.create_workspace_for_root(root_path=root_path)
    reused = service.create_workspace_for_root(root_path=root_path)

    assert created.workspace_id == "project-root"
    assert reused.workspace_id == "project-root"
    assert len(service.list_workspaces()) == 1


def test_workspace_service_create_for_root_generates_unique_workspace_id(
    tmp_path: Path,
) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    first_root = tmp_path / "Demo Project"
    second_root = tmp_path / "demo-project"
    first_root.mkdir()
    second_root.mkdir()

    first = service.create_workspace_for_root(root_path=first_root)
    second = service.create_workspace_for_root(root_path=second_root)

    assert first.workspace_id == "demo-project"
    assert second.workspace_id == "demo-project-2"


def test_workspace_service_deletes_workspace(tmp_path: Path) -> None:
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db")
    )
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    service.delete_workspace("project-alpha")

    assert service.list_workspaces() == ()
