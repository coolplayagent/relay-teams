# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.commands import (
    CommandCreateRequest,
    CommandCreateScope,
    CommandCreateSource,
    CommandManagementService,
    CommandRegistry,
    CommandUpdateRequest,
)
from relay_teams.workspace import (
    WorkspaceLocalMountConfig,
    WorkspaceMountCapabilities,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRepository,
    WorkspaceService,
    WorkspaceSshMountConfig,
    build_local_workspace_mount,
)


def test_create_returns_command_from_new_source_path_when_name_is_shadowed(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    relay_command_dir = workspace_root / ".relay-teams" / "commands"
    relay_command_dir.mkdir(parents=True)
    (relay_command_dir / "shared.md").write_text(
        "---\nname: shared\n---\nExisting high precedence command",
        encoding="utf-8",
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    response = service.create_command(
        CommandCreateRequest(
            scope=CommandCreateScope.PROJECT,
            workspace_id="workspace-1",
            source=CommandCreateSource.CODEX,
            relative_path="shared.md",
            name="shared",
            template="New codex command",
        )
    )

    target_path = workspace_root / ".codex" / "commands" / "shared.md"
    assert response.command.source_path == target_path.resolve()
    assert response.command.template == "New codex command"


def test_update_uses_mount_that_owns_workspace_root_for_writable_scope(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    other_root = tmp_path / "other"
    workspace_root = tmp_path / "workspace"
    other_root.mkdir(parents=True)
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        mounts=(
            build_local_workspace_mount(mount_name="aaa", root_path=other_root),
            build_local_workspace_mount(mount_name="zzz", root_path=workspace_root),
        ),
        default_mount_name="zzz",
    )
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    command_path = command_dir / "review.md"
    command_path.write_text("Review", encoding="utf-8")
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    response = service.update_command(
        CommandUpdateRequest(
            source_path=command_path,
            name="review",
            template="Updated review",
        )
    )

    assert response.workspace_id == "workspace-1"
    assert response.command.source_path == command_path.resolve()
    assert response.command.template == "Updated review"


def test_create_project_command_uses_local_mount_when_default_mount_is_ssh(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        mounts=(
            WorkspaceMountRecord(
                mount_name="default",
                provider=WorkspaceMountProvider.SSH,
                provider_config=WorkspaceSshMountConfig(
                    ssh_profile_id="prod",
                    remote_root="/srv/app",
                ),
            ),
            build_local_workspace_mount(mount_name="local", root_path=workspace_root),
        ),
        default_mount_name="default",
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    response = service.create_command(
        CommandCreateRequest(
            scope=CommandCreateScope.PROJECT,
            workspace_id="workspace-1",
            source=CommandCreateSource.RELAY_TEAMS,
            relative_path="ops/propose.md",
            name="ops:propose",
            template="Propose {{args}}",
        )
    )

    target_path = workspace_root / ".relay-teams" / "commands" / "ops" / "propose.md"
    assert response.command.source_path == target_path.resolve()
    assert target_path.exists()


def test_catalog_marks_read_only_workspace_as_not_creatable(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        mounts=(
            WorkspaceMountRecord(
                mount_name="default",
                provider=WorkspaceMountProvider.LOCAL,
                provider_config=WorkspaceLocalMountConfig(root_path=workspace_root),
                capabilities=WorkspaceMountCapabilities(can_write=False),
            ),
        ),
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    catalog = service.catalog()

    assert catalog.workspaces[0].root_path == workspace_root.resolve()
    assert catalog.workspaces[0].can_create_commands is False


def test_catalog_respects_workspace_writable_path_scope(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=workspace_root,
                writable_paths=("src",),
            ),
        ),
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    catalog = service.catalog()

    assert catalog.workspaces[0].can_create_commands is False


def test_catalog_allows_workspace_command_subdirectory_scope(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        mounts=(
            build_local_workspace_mount(
                mount_name="default",
                root_path=workspace_root,
                writable_paths=(".relay-teams/commands/ops",),
            ),
        ),
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    catalog = service.catalog()

    assert catalog.workspaces[0].can_create_commands is True


def test_create_opencode_command_uses_plural_commands_directory(
    tmp_path: Path,
) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    legacy_command_dir = workspace_root / ".opencode" / "command"
    legacy_command_dir.mkdir(parents=True)
    (legacy_command_dir / "shared.md").write_text(
        "---\nname: shared\n---\nLegacy command",
        encoding="utf-8",
    )
    registry = CommandRegistry(app_config_dir=app_config_dir)
    service = CommandManagementService(
        registry=registry,
        workspace_service=workspace_service,
    )

    response = service.create_command(
        CommandCreateRequest(
            scope=CommandCreateScope.PROJECT,
            workspace_id="workspace-1",
            source=CommandCreateSource.OPENCODE,
            relative_path="shared.md",
            name="shared",
            template="New opencode command",
        )
    )

    target_path = workspace_root / ".opencode" / "commands" / "shared.md"
    resolved_command = registry.get_command(
        "shared",
        workspace_root=workspace_root,
    )
    assert response.command.source_path == target_path.resolve()
    assert target_path.exists()
    assert resolved_command is not None
    assert resolved_command.source_path == target_path.resolve()
    assert resolved_command.template == "New opencode command"


def test_create_rejects_paths_deeper_than_discovery_limit(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )
    relative_path = "/".join(("deep",) * 9 + ("review.md",))

    try:
        service.create_command(
            CommandCreateRequest(
                scope=CommandCreateScope.PROJECT,
                workspace_id="workspace-1",
                source=CommandCreateSource.RELAY_TEAMS,
                relative_path=relative_path,
                name="deep:review",
                template="Review",
            )
        )
    except ValueError as exc:
        assert "relative .md path" in str(exc)
    else:
        raise AssertionError("expected deep command path to be rejected")

    assert not (workspace_root / ".relay-teams" / "commands").exists()


def test_create_rejects_uppercase_markdown_extension(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    try:
        service.create_command(
            CommandCreateRequest(
                scope=CommandCreateScope.PROJECT,
                workspace_id="workspace-1",
                source=CommandCreateSource.RELAY_TEAMS,
                relative_path="review.MD",
                name="review",
                template="Review",
            )
        )
    except ValueError as exc:
        assert "relative .md path" in str(exc)
    else:
        raise AssertionError("expected uppercase markdown extension to be rejected")


def test_update_rejects_paths_deeper_than_discovery_limit(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    command_path = workspace_root / ".relay-teams" / "commands"
    for part in ("deep",) * 9:
        command_path /= part
    command_path.mkdir(parents=True)
    source_path = command_path / "review.md"
    source_path.write_text("Review", encoding="utf-8")
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    try:
        service.update_command(
            CommandUpdateRequest(
                source_path=source_path,
                name="deep:review",
                template="Updated",
            )
        )
    except ValueError as exc:
        assert "exceeds discovery depth" in str(exc)
    else:
        raise AssertionError("expected deep command path to be rejected")


def test_update_rejects_uppercase_markdown_extension(tmp_path: Path) -> None:
    app_config_dir = tmp_path / "app"
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    workspace_service = _workspace_service(tmp_path)
    workspace_service.create_workspace(
        workspace_id="workspace-1",
        root_path=workspace_root,
    )
    command_dir = workspace_root / ".relay-teams" / "commands"
    command_dir.mkdir(parents=True)
    source_path = command_dir / "review.MD"
    source_path.write_text("Review", encoding="utf-8")
    service = CommandManagementService(
        registry=CommandRegistry(app_config_dir=app_config_dir),
        workspace_service=workspace_service,
    )

    try:
        service.update_command(
            CommandUpdateRequest(
                source_path=source_path,
                name="review",
                template="Updated",
            )
        )
    except ValueError as exc:
        assert "must be a .md file" in str(exc)
    else:
        raise AssertionError("expected uppercase markdown extension to be rejected")


def _workspace_service(tmp_path: Path) -> WorkspaceService:
    return WorkspaceService(repository=WorkspaceRepository(tmp_path / "workspace.db"))
