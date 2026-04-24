# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileRepository,
    SshProfileStoredConfig,
    SshProfileService,
    WorkspaceLocalMountConfig,
    WorkspaceMountRecord,
    WorkspaceMountProvider,
    WorkspaceRepository,
    WorkspaceService,
    WorkspaceSshMountConfig,
)


def test_ssh_profile_repository_save_get_list_and_delete(tmp_path: Path) -> None:
    repository = SshProfileRepository(tmp_path / "workspace.db")

    saved = repository.save(
        ssh_profile_id="prod",
        config=SshProfileStoredConfig(
            host="prod-alias",
            username="deploy",
            port=22,
            remote_shell="/bin/bash",
            connect_timeout_seconds=15,
            private_key_name="id_ed25519",
        ),
    )

    fetched = repository.get("prod")
    listed = repository.list_all()

    assert saved.ssh_profile_id == "prod"
    assert fetched.host == "prod-alias"
    assert fetched.username == "deploy"
    assert fetched.private_key_name == "id_ed25519"
    assert [item.ssh_profile_id for item in listed] == ["prod"]

    repository.delete("prod")

    with pytest.raises(KeyError):
        repository.get("prod")


def test_workspace_service_requires_existing_ssh_profile(tmp_path: Path) -> None:
    ssh_profile_service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db"),
        config_dir=tmp_path,
    )
    workspace_service = WorkspaceService(
        repository=WorkspaceRepository(tmp_path / "workspace-workspaces.db"),
        ssh_profile_service=ssh_profile_service,
    )
    mount = WorkspaceMountRecord(
        mount_name="prod",
        provider=WorkspaceMountProvider.SSH,
        provider_config=WorkspaceSshMountConfig(
            ssh_profile_id="prod",
            remote_root="/srv/app",
        ),
    )
    local_root = tmp_path / "workspace-root"
    local_root.mkdir()

    with pytest.raises(
        ValueError,
        match="Workspace ssh mount references unknown ssh profile: prod",
    ):
        workspace_service.create_workspace(
            workspace_id="project-alpha",
            mounts=(
                WorkspaceMountRecord(
                    mount_name="default",
                    provider=WorkspaceMountProvider.LOCAL,
                    provider_config=WorkspaceLocalMountConfig(
                        root_path=local_root.resolve()
                    ),
                ),
                mount,
            ),
            default_mount_name="default",
        )

    _ = ssh_profile_service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(host="prod-alias", username="deploy"),
    )

    remote_default = workspace_service.create_workspace(
        workspace_id="remote-project",
        mounts=(mount,),
        default_mount_name="prod",
    )

    assert remote_default.default_mount_name == "prod"

    created = workspace_service.create_workspace(
        workspace_id="project-alpha",
        mounts=(
            WorkspaceMountRecord(
                mount_name="default",
                provider=WorkspaceMountProvider.LOCAL,
                provider_config=WorkspaceLocalMountConfig(
                    root_path=local_root.resolve()
                ),
            ),
            mount,
        ),
        default_mount_name="default",
    )

    assert created.default_mount_name == "default"
    assert [item.provider.value for item in created.mounts] == ["local", "ssh"]


def test_workspace_ssh_mount_config_rejects_blank_remote_root() -> None:
    with pytest.raises(ValueError, match="remote_root must not be empty"):
        _ = WorkspaceSshMountConfig(
            ssh_profile_id="prod",
            remote_root="   ",
        )
