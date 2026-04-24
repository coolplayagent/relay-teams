# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.workspace import WorkspaceHandle
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRef,
    WorkspaceRemoteMountRoot,
    WorkspaceSshMountConfig,
    build_local_workspace_mount,
)


def _build_workspace_handle(
    *,
    workspace_dir: Path,
    scope_root: Path,
    execution_root: Path | None = None,
    readable_roots: tuple[Path, ...] | None = None,
    writable_roots: tuple[Path, ...] | None = None,
) -> WorkspaceHandle:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    scope_root.mkdir(parents=True, exist_ok=True)
    resolved_execution_root = execution_root or scope_root
    resolved_execution_root.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_dir / "tmp"
    return WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            default_mount_name="default",
            mount_names=("default",),
        ),
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=scope_root,
                working_directory=(
                    resolved_execution_root.relative_to(scope_root).as_posix()
                    if resolved_execution_root != scope_root
                    else "."
                ),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="default",
            provider=WorkspaceMountProvider.LOCAL,
            scope_root=scope_root,
            execution_root=resolved_execution_root,
            tmp_root=tmp_root,
            readable_roots=readable_roots or (scope_root, tmp_root),
            writable_roots=writable_roots or (scope_root, tmp_root),
        ),
    )


def test_resolve_read_path_allows_absolute_path_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    external_file = tmp_path / "external" / "notes.txt"
    external_file.parent.mkdir(parents=True)
    external_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_read_path(str(external_file))

    assert resolved == external_file.resolve()


def test_resolve_workspace_path_allows_absolute_path_in_extra_read_root(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    extra_read_root = tmp_path / "skills"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
        readable_roots=(workspace_root, workspace_root / "tmp", extra_read_root),
    )
    external_file = extra_read_root / "notes.txt"
    external_file.parent.mkdir(parents=True, exist_ok=True)
    external_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_workspace_path(str(external_file), write=False)

    assert resolved.mount_name == "default"
    assert resolved.host_bypass is False
    assert resolved.local_path == external_file.resolve()
    assert resolved.logical_path == external_file.resolve().as_posix()


def test_resolve_read_path_allows_absolute_path_inside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    workspace_file = workspace_root / "notes.txt"
    workspace_file.write_text("notes\n", encoding="utf-8")

    resolved = workspace.resolve_read_path(str(workspace_file.resolve()))

    assert resolved == workspace_file.resolve()


def test_resolve_read_path_rejects_relative_escape_outside_workspace(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )
    external_file = tmp_path / "outside.txt"
    external_file.write_text("outside\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside workspace read scope"):
        workspace.resolve_read_path("../outside.txt")


def test_resolve_workdir_rejects_outside_path(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )

    with pytest.raises(ValueError, match="outside workspace write scope"):
        workspace.resolve_workdir("../outside")


def test_resolve_workdir_rejects_non_local_mount(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    scope_root = tmp_path / "scope"
    workspace_root.mkdir(parents=True, exist_ok=True)
    scope_root.mkdir(parents=True, exist_ok=True)
    tmp_root = workspace_root / "tmp"
    workspace = WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            default_mount_name="remote",
            mount_names=("remote",),
        ),
        mounts=(
            WorkspaceMountRecord(
                mount_name="remote",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="ssh-profile",
                    remote_root="/srv/project",
                ),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_root,
            mount_name="remote",
            provider=WorkspaceMountProvider.SSH,
            scope_root=scope_root,
            execution_root=scope_root,
            tmp_root=tmp_root,
            readable_roots=(scope_root, tmp_root),
            writable_roots=(scope_root, tmp_root),
        ),
    )

    with pytest.raises(
        ValueError, match="Workspace workdir resolves to non-local mount: remote"
    ):
        workspace.resolve_workdir("app")


def test_resolve_path_rejects_write_outside_workspace_with_allowed_roots(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_root,
        scope_root=workspace_root,
    )

    with pytest.raises(ValueError) as exc_info:
        workspace.resolve_path("../outside.txt", write=True)

    message = str(exc_info.value)
    assert "outside workspace write scope" in message
    assert "requested=../outside.txt" in message
    assert f"resolved={(tmp_path / 'outside.txt').resolve()}" in message
    assert str(workspace_root.resolve()) in message
    assert str((workspace_root / "tmp").resolve()) in message


def test_resolve_path_routes_tmp_prefix_to_managed_tmp_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
    )

    resolved = workspace.resolve_path("tmp/reports/spec.md", write=True)

    assert resolved == (workspace_dir / "tmp" / "reports" / "spec.md").resolve()


def test_resolve_path_keeps_dot_tmp_inside_execution_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    execution_root = scope_root / "app"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
        execution_root=execution_root,
    )

    resolved = workspace.resolve_path("./tmp/spec.md", write=True)

    assert resolved == (execution_root / "tmp" / "spec.md").resolve()


def test_resolve_path_allows_parent_navigation_within_scope_root(
    tmp_path: Path,
) -> None:
    scope_root = tmp_path / "project"
    execution_root = scope_root / "app" / "backend"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
        execution_root=execution_root,
    )

    resolved = workspace.resolve_path("../README.md", write=False)

    assert resolved == (scope_root / "app" / "README.md").resolve()


def test_resolve_workdir_routes_tmp_prefix_to_managed_tmp_root(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
    )

    assert workspace.resolve_workdir("tmp") == (workspace_dir / "tmp").resolve()


def test_resolve_workdir_caches_resolved_local_path(tmp_path: Path) -> None:
    scope_root = tmp_path / "project"
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    workspace = _build_workspace_handle(
        workspace_dir=workspace_dir,
        scope_root=scope_root,
    )
    first = workspace.resolve_workdir("tmp")
    second = workspace.resolve_workdir("tmp")

    assert first == second
    assert workspace._resolved_workdirs["tmp"] == first


def test_resolve_ssh_mount_path_uses_materialized_local_root(tmp_path: Path) -> None:
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    remote_local_root = workspace_dir / "ssh_mounts" / "prod"
    tmp_root = workspace_dir / "tmp"
    remote_local_root.mkdir(parents=True)
    tmp_root.mkdir(parents=True)
    remote_file = remote_local_root / "src" / "app.py"
    remote_file.parent.mkdir(parents=True)
    remote_file.write_text("print('remote')\n", encoding="utf-8")
    workspace = WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            default_mount_name="prod",
            mount_names=("prod",),
        ),
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
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="prod",
            provider=WorkspaceMountProvider.SSH,
            scope_root=remote_local_root,
            execution_root=remote_local_root,
            tmp_root=tmp_root,
            readable_roots=(remote_local_root, tmp_root),
            writable_roots=(remote_local_root, tmp_root),
            remote_mount_roots=(
                WorkspaceRemoteMountRoot(
                    mount_name="prod",
                    local_root=remote_local_root,
                    remote_root="/srv/app",
                ),
            ),
        ),
    )

    resolved = workspace.resolve_workspace_path("src/app.py", write=False)

    assert resolved.provider == WorkspaceMountProvider.SSH
    assert resolved.local_path == remote_file.resolve()
    assert resolved.remote_path == "/srv/app/src/app.py"
    assert workspace.resolve_read_path("src/app.py") == remote_file.resolve()
    assert workspace.resolve_read_path("/srv/app/src/app.py") == remote_file.resolve()
    assert (
        workspace.resolve_path("/srv/app/src/app.py", write=True)
        == remote_file.resolve()
    )


def test_resolve_absolute_local_path_wins_over_broad_ssh_remote_root(
    tmp_path: Path,
) -> None:
    if not Path("/").is_absolute():
        pytest.skip("Platform treats POSIX-rooted SSH paths as relative")
    workspace_dir = tmp_path / ".agent-teams" / "workspaces" / "project"
    local_root = tmp_path / "project"
    remote_local_root = workspace_dir / "ssh_mounts" / "prod"
    tmp_root = workspace_dir / "tmp"
    local_file = local_root / "notes.txt"
    local_file.parent.mkdir(parents=True)
    local_file.write_text("local\n", encoding="utf-8")
    remote_local_root.mkdir(parents=True)
    tmp_root.mkdir(parents=True)
    workspace = WorkspaceHandle(
        ref=WorkspaceRef(
            workspace_id="workspace",
            session_id="session",
            role_id="role",
            conversation_id="conversation",
            default_mount_name="default",
            mount_names=("default", "prod"),
        ),
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=local_root,
                working_directory=".",
            ),
            WorkspaceMountRecord(
                mount_name="prod",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod",
                    remote_root="/",
                ),
            ),
        ),
        locations=WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name="default",
            provider=WorkspaceMountProvider.LOCAL,
            scope_root=local_root,
            execution_root=local_root,
            tmp_root=tmp_root,
            readable_roots=(local_root, remote_local_root, tmp_root),
            writable_roots=(local_root, remote_local_root, tmp_root),
            remote_mount_roots=(
                WorkspaceRemoteMountRoot(
                    mount_name="prod",
                    local_root=remote_local_root,
                    remote_root="/",
                ),
            ),
        ),
    )

    resolved = workspace.resolve_workspace_path(str(local_file), write=False)

    assert resolved.provider == WorkspaceMountProvider.LOCAL
    assert resolved.mount_name == "default"
    assert resolved.local_path == local_file.resolve()
    assert resolved.remote_path is None
