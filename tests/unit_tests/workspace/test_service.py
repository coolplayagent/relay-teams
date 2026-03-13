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
