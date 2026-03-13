# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_teams.interfaces.server.deps import get_workspace_service
from agent_teams.interfaces.server.routers import workspaces
from agent_teams.workspace import WorkspaceRepository, WorkspaceService


def _create_test_client(tmp_path: Path) -> tuple[TestClient, WorkspaceService]:
    app = FastAPI()
    app.include_router(workspaces.router, prefix="/api")
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
    )
    app.dependency_overrides[get_workspace_service] = lambda: service
    return TestClient(app), service


def test_create_workspace(tmp_path: Path) -> None:
    client, _ = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()

    response = client.post(
        "/api/workspaces",
        json={
            "workspace_id": "project-alpha",
            "root_path": str(root_path),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == "project-alpha"
    assert payload["root_path"] == str(root_path.resolve())


def test_list_and_get_workspaces(tmp_path: Path) -> None:
    client, service = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    list_response = client.get("/api/workspaces")
    get_response = client.get("/api/workspaces/project-alpha")

    assert list_response.status_code == 200
    assert [item["workspace_id"] for item in list_response.json()] == ["project-alpha"]
    assert get_response.status_code == 200
    assert get_response.json()["root_path"] == str(root_path.resolve())


def test_create_workspace_rejects_missing_root(tmp_path: Path) -> None:
    client, _ = _create_test_client(tmp_path)

    response = client.post(
        "/api/workspaces",
        json={
            "workspace_id": "missing-root",
            "root_path": str(tmp_path / "missing"),
        },
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["detail"]


def test_pick_workspace_creates_workspace_for_selected_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _create_test_client(tmp_path)
    root_path = tmp_path / "picked-root"
    root_path.mkdir()

    monkeypatch.setattr(
        workspaces,
        "pick_workspace_directory",
        lambda: root_path,
    )

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["workspace_id"] == "picked-root"
    assert payload["workspace"]["root_path"] == str(root_path.resolve())


def test_pick_workspace_returns_null_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = _create_test_client(tmp_path)

    monkeypatch.setattr(
        workspaces,
        "pick_workspace_directory",
        lambda: None,
    )

    response = client.post("/api/workspaces/pick")

    assert response.status_code == 200
    assert response.json() == {"workspace": None}
