# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

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
        self.ensure_calls: list[Path] = []
        self.head_calls: list[Path] = []
        self.add_calls: list[tuple[Path, str, Path, str]] = []
        self.remove_calls: list[tuple[Path, Path]] = []
        self.prune_calls: list[Path] = []

    def ensure_repository(self, repository_root: Path) -> Path:
        self.ensure_calls.append(repository_root)
        return repository_root.resolve()

    def current_head(self, repository_root: Path) -> str:
        self.head_calls.append(repository_root)
        return "abc123"

    def add_worktree(
        self,
        *,
        repository_root: Path,
        branch_name: str,
        target_path: Path,
        start_point: str,
    ) -> None:
        self.add_calls.append((repository_root, branch_name, target_path, start_point))
        target_path.mkdir(parents=True, exist_ok=True)

    def remove_worktree(self, *, repository_root: Path, target_path: Path) -> None:
        self.remove_calls.append((repository_root, target_path))
        shutil.rmtree(target_path, ignore_errors=True)

    def prune(self, repository_root: Path) -> None:
        self.prune_calls.append(repository_root)


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


def test_workspace_service_forks_workspace_into_git_worktree(tmp_path: Path) -> None:
    root_path = tmp_path / "workspace-root"
    root_path.mkdir()
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    _ = service.create_workspace(
        workspace_id="project-alpha",
        root_path=root_path,
    )

    created = service.fork_workspace(
        source_workspace_id="project-alpha",
        name="Alpha Project Fork",
    )

    assert created.workspace_id == "alpha-project-fork"
    assert (
        created.root_path
        == (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve()
    )
    assert created.profile.file_scope.backend == FileScopeBackend.GIT_WORKTREE
    assert created.profile.file_scope.branch_name == "fork/alpha-project-fork"
    assert created.profile.file_scope.source_root_path == str(root_path.resolve())
    assert created.profile.file_scope.forked_from_workspace_id == "project-alpha"
    assert git_client.ensure_calls == [root_path.resolve()]
    assert git_client.head_calls == [root_path.resolve()]
    assert git_client.add_calls == [
        (
            root_path.resolve(),
            "fork/alpha-project-fork",
            (tmp_path / "storage" / "alpha-project-fork" / "worktree").resolve(),
            "abc123",
        )
    ]


def test_workspace_service_deletes_git_worktree_when_requested(tmp_path: Path) -> None:
    git_client = FakeGitWorktreeClient()
    service = StorageScopedWorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace.db"),
        storage_root=tmp_path / "storage",
        git_worktree_client=git_client,
    )
    root_path = tmp_path / "storage" / "alpha-project-fork" / "worktree"
    root_path.mkdir(parents=True)
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

    deleted = service.delete_workspace_with_options(
        workspace_id="alpha-project-fork",
        remove_worktree=True,
    )

    assert deleted.workspace_id == "alpha-project-fork"
    assert git_client.remove_calls == [
        ((tmp_path / "workspace-root").resolve(), root_path.resolve())
    ]
    assert git_client.prune_calls == [(tmp_path / "workspace-root").resolve()]
    assert service.list_workspaces() == ()


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
