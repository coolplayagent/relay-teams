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
        app_config_dir=tmp_path / ".agent-teams",
        workspace_repo=WorkspaceRepository(db_path),
    )

    handle = manager.resolve(
        session_id="session-1",
        role_id="designer",
        instance_id=None,
        workspace_id="alpha-project-fork",
    )

    assert handle.locations.execution_root == worktree_root.resolve()
    assert handle.locations.readable_roots == (
        worktree_root.resolve(),
        (manager.locations_for("alpha-project-fork").workspace_dir / "tmp").resolve(),
    )
    assert handle.locations.writable_roots == (worktree_root.resolve(),)
    assert handle.locations.worktree_root == worktree_root.resolve()
    assert (
        manager.session_artifact_dir(
            workspace_id="alpha-project-fork",
            session_id="session-1",
        )
        == (
            tmp_path / ".agent-teams" / "sessions" / "alpha-project-fork" / "session-1"
        ).resolve()
    )


def test_workspace_manager_includes_builtin_and_app_skill_roots_in_read_scope(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace.db"
    project_root = tmp_path / "project"
    builtin_skills_dir = tmp_path / "builtin" / "skills"
    app_skills_dir = tmp_path / ".agent-teams" / "skills"
    project_root.mkdir(parents=True)
    builtin_skills_dir.mkdir(parents=True)
    app_skills_dir.mkdir(parents=True)

    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = service.create_workspace(
        workspace_id="default",
        root_path=project_root,
    )
    manager = WorkspaceManager(
        project_root=project_root,
        app_config_dir=tmp_path / ".agent-teams",
        workspace_repo=WorkspaceRepository(db_path),
        builtin_skills_dir=builtin_skills_dir,
        app_skills_dir=app_skills_dir,
    )

    handle = manager.resolve(
        session_id="session-1",
        role_id="designer",
        instance_id=None,
        workspace_id="default",
    )

    assert handle.locations.readable_roots == (
        project_root.resolve(),
        (manager.locations_for("default").workspace_dir / "tmp").resolve(),
        builtin_skills_dir.resolve(),
        app_skills_dir.resolve(),
    )
    assert (
        handle.resolve_workdir(app_skills_dir.resolve().as_posix())
        == app_skills_dir.resolve()
    )
