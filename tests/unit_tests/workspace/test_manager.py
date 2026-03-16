# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workspace import (
    FileScopeBackend,
    WorkspaceFileScope,
    WorkspaceManager,
    WorkspaceProfile,
    WorkspaceRepository,
    WorkspaceService,
)


def test_workspace_manager_uses_worktree_root_for_git_worktree_workspace(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace.db"
    worktree_root = tmp_path / "storage" / "alpha-project-fork" / "worktree"
    worktree_root.mkdir(parents=True)
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = service.create_workspace(
        workspace_id="alpha-project-fork",
        root_path=worktree_root,
        profile=WorkspaceProfile(
            file_scope=WorkspaceFileScope(
                backend=FileScopeBackend.GIT_WORKTREE,
                branch_name="fork/alpha-project-fork",
                source_root_path=str((tmp_path / "source-root").resolve()),
                forked_from_workspace_id="project-alpha",
            )
        ),
    )
    manager = WorkspaceManager(
        project_root=tmp_path,
        workspace_repo=WorkspaceRepository(db_path),
    )

    handle = manager.resolve(
        session_id="session-1",
        role_id="designer",
        instance_id=None,
        workspace_id="alpha-project-fork",
    )

    assert handle.locations.execution_root == worktree_root.resolve()
    assert handle.locations.readable_roots == (worktree_root.resolve(),)
    assert handle.locations.writable_roots == (worktree_root.resolve(),)
    assert handle.locations.worktree_root == worktree_root.resolve()
    assert (
        manager.session_artifact_dir(
            workspace_id="alpha-project-fork",
            session_id="session-1",
        )
        == worktree_root.resolve() / ".agent_teams" / "sessions" / "session-1"
    )
