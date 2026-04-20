# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.workspace import (
    SshProfileConfig,
    SshProfileRepository,
    SshProfileService,
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
        config=SshProfileConfig(
            host="prod-alias",
            username="deploy",
            port=22,
            remote_shell="/bin/bash",
            connect_timeout_seconds=15,
        ),
    )

    fetched = repository.get("prod")
    listed = repository.list_all()

    assert saved.ssh_profile_id == "prod"
    assert fetched.host == "prod-alias"
    assert fetched.username == "deploy"
    assert [item.ssh_profile_id for item in listed] == ["prod"]

    repository.delete("prod")

    with pytest.raises(KeyError):
        repository.get("prod")


def test_workspace_service_requires_existing_ssh_profile(tmp_path: Path) -> None:
    ssh_profile_service = SshProfileService(
        repository=SshProfileRepository(tmp_path / "workspace.db")
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

    with pytest.raises(KeyError):
        workspace_service.create_workspace(
            workspace_id="project-alpha",
            mounts=(mount,),
            default_mount_name="prod",
        )

    _ = ssh_profile_service.save_profile(
        ssh_profile_id="prod",
        config=SshProfileConfig(host="prod-alias"),
    )

    created = workspace_service.create_workspace(
        workspace_id="project-alpha",
        mounts=(mount,),
        default_mount_name="prod",
    )

    assert created.default_mount_name == "prod"
    assert created.mounts[0].provider.value == "ssh"
