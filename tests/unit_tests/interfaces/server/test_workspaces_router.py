# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_teams.interfaces.server.deps import get_workspace_service
from agent_teams.interfaces.server.routers import workspaces
from agent_teams.workspace import (
    FileScopeBackend,
    GitWorktreeClient,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRepository,
    WorkspaceService,
)


class StorageScopedWorkspaceService(WorkspaceService):
    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        storage_root: Path,
        git_worktree_client: GitWorktreeClient | None = None,
    ) -> None:
        super().__init__(
            repository=repository,
            git_worktree_client=git_worktree_client,
        )
        self._storage_root = storage_root

    def _workspace_storage_dir(self, workspace_id: str) -> Path:
        return self._storage_root / workspace_id


class FakeGitWorktreeClient(GitWorktreeClient):
    def __init__(self) -> None:
        self.remove_calls: list[tuple[Path, Path]] = []

    def ensure_repository(self, repository_root: Path) -> Path:
        return repository_root.resolve()

    def current_head(self, repository_root: Path) -> str:
        return "abc123"

    def add_worktree(
        self,
        *,
        repository_root: Path,
        branch_name: str,
        target_path: Path,
        start_point: str,
    ) -> None:
        _ = (repository_root, branch_name, start_point)
        target_path.mkdir(parents=True, exist_ok=True)

    def remove_worktree(self, *, repository_root: Path, target_path: Path) -> None:
        self.remove_calls.append((repository_root, target_path))
        shutil.rmtree(target_path, ignore_errors=True)

    def prune(self, repository_root: Path) -> None:
        _ = repository_root


def _create_test_client(
    tmp_path: Path,
    *,
    service: WorkspaceService | None = None,
) -> tuple[TestClient, WorkspaceService]:
    app = FastAPI()
    app.include_router(workspaces.router, prefix="/api")
    resolved_service = service or WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
    )
    app.dependency_overrides[get_workspace_service] = lambda: resolved_service
    return TestClient(app), resolved_service


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


def test_pick_workspace_creates_workspace_for_provided_root_path(
    tmp_path: Path,
) -> None:
    client, _ = _create_test_client(tmp_path)
    root_path = tmp_path / "provided-root"
    root_path.mkdir()

    response = client.post(
        "/api/workspaces/pick",
        json={"root_path": str(root_path)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"]["workspace_id"] == "provided-root"
    assert payload["workspace"]["root_path"] == str(root_path.resolve())


def test_fork_workspace(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=FakeGitWorktreeClient(),
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    client, _ = _create_test_client(tmp_path, service=service)

    response = client.post(
        "/api/workspaces/project-alpha:fork",
        json={"name": "Alpha Project Fork"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace_id"] == "alpha-project-fork"
    assert payload["root_path"] == str(
        (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve()
    )
    assert payload["profile"]["file_scope"]["backend"] == "git_worktree"
    assert payload["profile"]["file_scope"]["branch_name"] == "fork/alpha-project-fork"


def test_delete_workspace_supports_remove_worktree_query(tmp_path: Path) -> None:
    root_path = tmp_path / "storage" / "alpha-project-fork" / "worktree"
    root_path.mkdir(parents=True)
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    _ = service.create_workspace(
        workspace_id="alpha-project-fork",
        root_path=root_path,
        profile=WorkspaceProfile(
            file_scope=WorkspaceFileScope(
                backend=FileScopeBackend.GIT_WORKTREE,
                branch_name="fork/alpha-project-fork",
                source_root_path=str((tmp_path / "workspace-root").resolve()),
                forked_from_workspace_id="project-alpha",
            )
        ),
    )
    client, _ = _create_test_client(tmp_path, service=service)

    response = client.delete("/api/workspaces/alpha-project-fork?remove_worktree=true")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert git_client.remove_calls == [
        ((tmp_path / "workspace-root").resolve(), root_path.resolve())
    ]
    assert service.list_workspaces() == ()
