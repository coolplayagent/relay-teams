# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import relay_teams.workspace.workspace_service as workspace_service_module

from relay_teams.interfaces.server.deps import get_workspace_service
from relay_teams.interfaces.server.routers import workspaces
from relay_teams.workspace import (
    FileScopeBackend,
    GitWorktreeClient,
    WorkspaceRecord,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRepository,
    WorkspaceService,
    WorkspaceDiffChangeType,
    WorkspaceDiffFile,
    WorkspaceDiffListing,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    WorkspaceTreeNode,
    WorkspaceTreeNodeKind,
    build_local_workspace_mount,
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

    def fetch_ref(
        self,
        repository_root: Path,
        *,
        remote: str = "origin",
        ref: str = "main",
    ) -> None:
        _ = (repository_root, remote, ref)

    def resolve_ref(self, repository_root: Path, ref_name: str) -> str:
        _ = repository_root
        return f"resolved:{ref_name}"

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


def test_create_workspace_rejects_none_like_workspace_id(tmp_path: Path) -> None:
    client, _ = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()

    response = client.post(
        "/api/workspaces",
        json={
            "workspace_id": "None",
            "root_path": str(root_path),
        },
    )

    assert response.status_code == 422


def test_create_workspace_with_mounts_and_mount_scoped_tree_query(
    tmp_path: Path,
) -> None:
    client, _ = _create_test_client(tmp_path)
    app_root = tmp_path / "app-root"
    ops_root = tmp_path / "ops-root"
    app_root.mkdir()
    ops_root.mkdir()
    (ops_root / "deploy.sh").write_text("echo deploy\n", encoding="utf-8")

    response = client.post(
        "/api/workspaces",
        json={
            "workspace_id": "project-alpha",
            "default_mount_name": "app",
            "mounts": [
                {
                    "mount_name": "app",
                    "provider": "local",
                    "provider_config": {"root_path": str(app_root)},
                },
                {
                    "mount_name": "ops",
                    "provider": "local",
                    "provider_config": {"root_path": str(ops_root)},
                },
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_mount_name"] == "app"
    assert payload["root_path"] == str(app_root.resolve())
    assert [item["mount_name"] for item in payload["mounts"]] == ["app", "ops"]

    tree_response = client.get("/api/workspaces/project-alpha/tree?path=.&mount=ops")

    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["mount_name"] == "ops"
    assert [item["path"] for item in tree_payload["children"]] == ["deploy.sh"]


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


def test_get_workspace_rejects_none_like_path_identifier(tmp_path: Path) -> None:
    client, _ = _create_test_client(tmp_path)

    response = client.get("/api/workspaces/None")

    assert response.status_code == 422


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


def test_open_workspace_root_uses_native_directory_opener(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    captured: dict[str, Path] = {}

    def fake_open_workspace_directory(path: Path) -> None:
        captured["path"] = path

    monkeypatch.setattr(
        workspace_service_module,
        "open_workspace_directory",
        fake_open_workspace_directory,
    )

    response = client.post("/api/workspaces/project-alpha:open-root")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert captured["path"] == root_path.resolve()


def test_open_workspace_root_returns_service_unavailable_when_opener_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, service = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    def fail_open_workspace_directory(path: Path) -> None:
        _ = path
        raise RuntimeError("Native file manager is unavailable")

    monkeypatch.setattr(
        workspace_service_module,
        "open_workspace_directory",
        fail_open_workspace_directory,
    )

    response = client.post("/api/workspaces/project-alpha:open-root")

    assert response.status_code == 503
    assert response.json() == {"detail": "Native file manager is unavailable"}


def test_get_workspace_snapshot_tree_and_diffs(tmp_path: Path) -> None:
    class SnapshotWorkspaceService(WorkspaceService):
        def get_workspace_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
            record = self.get_workspace(workspace_id)
            root_path = record.root_path
            assert root_path is not None
            return WorkspaceSnapshot(
                workspace_id=record.workspace_id,
                default_mount_root=root_path,
                tree=WorkspaceTreeNode(
                    name=root_path.name,
                    path=".",
                    kind=WorkspaceTreeNodeKind.DIRECTORY,
                    has_children=True,
                    children=(
                        WorkspaceTreeNode(
                            name="src",
                            path="src",
                            kind=WorkspaceTreeNodeKind.DIRECTORY,
                            has_children=True,
                            children=(),
                        ),
                    ),
                ),
            )

        def get_workspace_tree_listing(
            self,
            workspace_id: str,
            *,
            directory_path: str,
            mount_name: str | None = None,
        ) -> WorkspaceTreeListing:
            _ = (workspace_id, mount_name)
            return WorkspaceTreeListing(
                workspace_id="project-alpha",
                directory_path=directory_path,
                children=(
                    WorkspaceTreeNode(
                        name="app.py",
                        path="src/app.py",
                        kind=WorkspaceTreeNodeKind.FILE,
                        has_children=False,
                        children=(),
                    ),
                ),
            )

        def get_workspace_diffs(
            self,
            workspace_id: str,
            *,
            mount_name: str | None = None,
        ) -> WorkspaceDiffListing:
            record = self.get_workspace(workspace_id)
            _ = mount_name
            root_path = record.root_path
            assert root_path is not None
            return WorkspaceDiffListing(
                workspace_id=record.workspace_id,
                root_path=root_path,
                diff_files=(),
                is_git_repository=True,
                git_root_path=root_path,
                diff_message=None,
            )

        def get_workspace_diff_file(
            self,
            workspace_id: str,
            *,
            path: str,
            mount_name: str | None = None,
        ) -> WorkspaceDiffFile:
            _ = (self.get_workspace(workspace_id), mount_name)
            return WorkspaceDiffFile(
                path=path,
                change_type=WorkspaceDiffChangeType.MODIFIED,
                diff="patched content",
                is_binary=False,
            )

    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = SnapshotWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    client, _ = _create_test_client(tmp_path, service=service)

    snapshot_response = client.get("/api/workspaces/project-alpha/snapshot")
    tree_response = client.get("/api/workspaces/project-alpha/tree?path=src")
    diffs_response = client.get("/api/workspaces/project-alpha/diffs")
    diff_file_response = client.get(
        "/api/workspaces/project-alpha/diff?path=src%2Fapp.py"
    )

    assert snapshot_response.status_code == 200
    snapshot_payload = snapshot_response.json()
    assert snapshot_payload["workspace_id"] == "project-alpha"
    assert snapshot_payload["root_path"] == str(root_path.resolve())
    assert snapshot_payload["tree"]["path"] == "."
    assert snapshot_payload["tree"]["children"][0]["has_children"] is True

    assert tree_response.status_code == 200
    tree_payload = tree_response.json()
    assert tree_payload["directory_path"] == "src"
    assert tree_payload["children"][0]["path"] == "src/app.py"

    assert diffs_response.status_code == 200
    diffs_payload = diffs_response.json()
    assert diffs_payload["workspace_id"] == "project-alpha"
    assert diffs_payload["is_git_repository"] is True
    assert diffs_payload["git_root_path"] == str(root_path.resolve())

    assert diff_file_response.status_code == 200
    diff_file_payload = diff_file_response.json()
    assert diff_file_payload["path"] == "src/app.py"
    assert diff_file_payload["diff"] == "patched content"


def test_get_workspace_preview_file_streams_workspace_image(tmp_path: Path) -> None:
    client, service = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    image_path = root_path / "artifacts" / "brief.png"
    image_path.parent.mkdir(parents=True)
    image_bytes = b"png-preview"
    image_path.write_bytes(image_bytes)
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    response = client.get(
        f"/api/workspaces/project-alpha/preview-file?path={image_path.as_posix()}"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == image_bytes


def test_get_workspace_preview_file_rejects_non_image(tmp_path: Path) -> None:
    client, service = _create_test_client(tmp_path)
    root_path = tmp_path / "workspace-root"
    file_path = root_path / "notes.txt"
    root_path.mkdir()
    file_path.write_text("hello\n", encoding="utf-8")
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    response = client.get("/api/workspaces/project-alpha/preview-file?path=notes.txt")

    assert response.status_code == 400
    assert "supported image" in response.json()["detail"]


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


def test_fork_workspace_forwards_start_ref(tmp_path: Path) -> None:
    class CaptureForkWorkspaceService(WorkspaceService):
        def __init__(self) -> None:
            super().__init__(
                repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
            )
            self.calls: list[tuple[str, str, str | None]] = []

        def fork_workspace(
            self,
            *,
            source_workspace_id: str,
            name: str,
            start_ref: str | None = None,
        ):
            self.calls.append((source_workspace_id, name, start_ref))
            return WorkspaceRecord(
                workspace_id="alpha-project-fork",
                default_mount_name="default",
                mounts=(
                    build_local_workspace_mount(
                        mount_name="default",
                        root_path=(
                            tmp_path / "storage" / "alpha-project-fork"
                        ).resolve(),
                    ),
                ),
            )

    service = CaptureForkWorkspaceService()
    client, _ = _create_test_client(tmp_path, service=service)

    response = client.post(
        "/api/workspaces/project-alpha:fork",
        json={"name": "Alpha Project Fork", "start_ref": "origin/release"},
    )

    assert response.status_code == 200
    assert service.calls == [("project-alpha", "Alpha Project Fork", "origin/release")]


def test_delete_workspace_requires_force_for_remove_worktree(tmp_path: Path) -> None:
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

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Cannot remove workspace directory without force"
    }
    assert git_client.remove_calls == []


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

    response = client.request(
        "DELETE",
        "/api/workspaces/alpha-project-fork?remove_worktree=true",
        json={"force": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert git_client.remove_calls == [
        ((tmp_path / "workspace-root").resolve(), root_path.resolve())
    ]
    assert service.list_workspaces() == ()


def test_delete_workspace_supports_remove_directory_query(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    client, _ = _create_test_client(tmp_path, service=service)

    response = client.request(
        "DELETE",
        "/api/workspaces/project-alpha?remove_directory=true",
        json={"force": True},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert root_path.exists() is False
    assert service.list_workspaces() == ()


def test_delete_workspace_returns_conflict_when_directory_removal_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspaces_router.db")
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )
    client, _ = _create_test_client(tmp_path, service=service)

    original_rmtree = shutil.rmtree

    def fail_rmtree(path: Path, ignore_errors: bool = False) -> None:
        _ = ignore_errors
        if Path(path) == root_path:
            raise PermissionError("permission denied")
        original_rmtree(path)

    monkeypatch.setattr(shutil, "rmtree", fail_rmtree)

    response = client.request(
        "DELETE",
        "/api/workspaces/project-alpha?remove_directory=true",
        json={"force": True},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": f"Failed to remove workspace path: {root_path}"
    }
    assert root_path.exists() is True
    assert [workspace.workspace_id for workspace in service.list_workspaces()] == [
        "project-alpha"
    ]
