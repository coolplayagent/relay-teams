# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from relay_teams.workspace import (
    FileScopeBackend,
    SshProfileConfig,
    SshProfileRepository,
    SshProfileService,
    WorkspaceFileScope,
    WorkspaceManager,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceProfile,
    WorkspaceRepository,
    WorkspaceService,
    WorkspaceSshMountConfig,
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

    tmp_root = (
        manager.locations_for("alpha-project-fork").workspace_dir / "tmp"
    ).resolve()

    assert handle.locations.scope_root == worktree_root.resolve()
    assert handle.locations.execution_root == worktree_root.resolve()
    assert handle.locations.readable_roots == (
        worktree_root.resolve(),
        tmp_root,
    )
    assert handle.locations.writable_roots == (worktree_root.resolve(), tmp_root)
    assert handle.locations.tmp_root == tmp_root
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

    tmp_root = (manager.locations_for("default").workspace_dir / "tmp").resolve()

    assert handle.locations.readable_roots == (
        project_root.resolve(),
        tmp_root,
        builtin_skills_dir.resolve(),
        app_skills_dir.resolve(),
    )
    assert handle.locations.writable_roots == (project_root.resolve(), tmp_root)
    with pytest.raises(ValueError, match="outside workspace write scope"):
        handle.resolve_workdir(app_skills_dir.resolve().as_posix())


def test_workspace_manager_resolves_execution_root_under_worktree_working_directory(
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
                working_directory="packages/app",
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

    assert handle.locations.scope_root == worktree_root.resolve()
    assert (
        handle.locations.execution_root
        == (worktree_root / "packages" / "app").resolve()
    )


def test_workspace_manager_materializes_default_ssh_mount(
    tmp_path: Path,
) -> None:
    captured_commands: list[tuple[str, ...]] = []

    def run_mount_command(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    db_path = tmp_path / "workspace.db"
    ssh_profile_service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "ssh_profiles.db"),
        config_dir=tmp_path,
        ssh_path_lookup=lambda name: f"/usr/bin/{name}",
        process_runner=run_mount_command,
    )
    _ = ssh_profile_service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(host="prod-alias"),
    )
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = service.create_workspace(
        workspace_id="remote-project",
        mounts=(
            WorkspaceMountRecord(
                mount_name="prod",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod",
                    remote_root="/srv/app",
                ),
            ),
        ),
        default_mount_name="prod",
    )
    manager = WorkspaceManager(
        project_root=tmp_path,
        app_config_dir=tmp_path / ".agent-teams",
        workspace_repo=WorkspaceRepository(db_path),
        ssh_profile_service=ssh_profile_service,
    )

    handle = manager.resolve(
        session_id="session-1",
        role_id="designer",
        instance_id=None,
        workspace_id="remote-project",
    )

    expected_local_root = (
        tmp_path
        / ".agent-teams"
        / "workspaces"
        / "remote-project"
        / "ssh_mounts"
        / "prod"
    ).resolve()
    assert captured_commands[0][0] == "/usr/bin/sshfs"
    assert captured_commands[0][1] == "prod-alias:/srv/app"
    assert captured_commands[0][2] == str(expected_local_root)
    assert handle.locations.provider == WorkspaceMountProvider.SSH
    assert handle.locations.scope_root == expected_local_root
    assert handle.locations.execution_root == expected_local_root
    assert (
        handle.resolve_path("src/app.py", write=True)
        == (expected_local_root / "src" / "app.py").resolve()
    )


def test_workspace_manager_rejects_ssh_mount_without_profile_service(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "workspace.db"
    service = WorkspaceService(repository=WorkspaceRepository(db_path))
    _ = service.create_workspace(
        workspace_id="remote-project",
        mounts=(
            WorkspaceMountRecord(
                mount_name="prod",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod",
                    remote_root="/srv/app",
                ),
            ),
        ),
        default_mount_name="prod",
    )
    manager = WorkspaceManager(
        project_root=tmp_path,
        app_config_dir=tmp_path / ".agent-teams",
        workspace_repo=WorkspaceRepository(db_path),
    )

    with pytest.raises(ValueError, match="requires ssh profile service"):
        _ = manager.resolve(
            session_id="session-1",
            role_id="designer",
            instance_id=None,
            workspace_id="remote-project",
        )
