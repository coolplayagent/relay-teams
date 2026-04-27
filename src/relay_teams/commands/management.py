# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Optional

from pydantic import BaseModel, ConfigDict
import yaml

from relay_teams.commands.command_models import (
    CommandCatalogResponse,
    CommandCatalogWorkspace,
    CommandCreateRequest,
    CommandCreateResponse,
    CommandCreateScope,
    CommandCreateSource,
    CommandDefinition,
    CommandUpdateRequest,
    CommandUpdateResponse,
    command_detail_from_definition,
)
from relay_teams.commands.discovery import (
    DEFAULT_COMMAND_MAX_DEPTH,
    is_valid_command_name,
)
from relay_teams.commands.registry import CommandRegistry
from relay_teams.workspace import (
    WorkspaceMountRecord,
    WorkspaceRecord,
    WorkspaceService,
)


class CommandManagementService:
    def __init__(
        self,
        *,
        registry: CommandRegistry,
        workspace_service: WorkspaceService,
    ) -> None:
        self.registry = registry
        self.workspace_service = workspace_service

    def catalog(self) -> CommandCatalogResponse:
        return CommandCatalogResponse(
            app_commands=tuple(
                command_detail_from_definition(command)
                for command in self.registry.list_app_commands()
            ),
            workspaces=tuple(
                self._catalog_workspace(workspace)
                for workspace in self.workspace_service.list_workspaces()
            ),
        )

    def create_command(self, req: CommandCreateRequest) -> CommandCreateResponse:
        name = _normalize_command_name(req.name)
        aliases = _normalize_aliases(req.aliases, command_name=name)
        relative_path = _normalize_relative_markdown_path(req.relative_path)
        allowed_modes = _normalize_allowed_modes(req.allowed_modes)
        if req.scope == CommandCreateScope.GLOBAL:
            target_path = self._resolve_global_target(relative_path)
            workspace_id: Optional[str] = None
            workspace_root: Optional[Path] = None
        else:
            workspace = self._resolve_create_workspace(req.workspace_id)
            source = req.source or CommandCreateSource.CLAUDE
            target_path = self._resolve_project_target(
                workspace=workspace,
                source=source,
                relative_path=relative_path,
            )
            workspace_id = workspace.workspace_id
            workspace_root = workspace.root_path

        if target_path.exists():
            raise FileExistsError(f"Command file already exists: {target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(
            _render_command_markdown(
                name=name,
                aliases=aliases,
                description=req.description,
                argument_hint=req.argument_hint,
                allowed_modes=allowed_modes,
                template=req.template,
            ),
            encoding="utf-8",
        )
        command = self._find_updated_command(
            target_path=target_path,
            workspace_root=workspace_root,
        )
        if command is None:
            raise RuntimeError(f"Created command could not be reloaded: {name}")
        return CommandCreateResponse(
            command=command_detail_from_definition(command),
            workspace_id=workspace_id,
        )

    def update_command(self, req: CommandUpdateRequest) -> CommandUpdateResponse:
        name = _normalize_command_name(req.name)
        aliases = _normalize_aliases(req.aliases, command_name=name)
        allowed_modes = _normalize_allowed_modes(req.allowed_modes)
        target = self._resolve_update_target(req.source_path)
        target.target_path.write_text(
            _render_command_markdown(
                name=name,
                aliases=aliases,
                description=req.description,
                argument_hint=req.argument_hint,
                allowed_modes=allowed_modes,
                template=req.template,
            ),
            encoding="utf-8",
        )
        command = self._find_updated_command(
            target_path=target.target_path,
            workspace_root=target.workspace_root,
        )
        if command is None:
            raise RuntimeError(f"Updated command could not be reloaded: {name}")
        return CommandUpdateResponse(
            command=command_detail_from_definition(command),
            workspace_id=target.workspace_id,
        )

    def _catalog_workspace(self, workspace: WorkspaceRecord) -> CommandCatalogWorkspace:
        workspace_root = workspace.root_path
        return CommandCatalogWorkspace(
            workspace_id=workspace.workspace_id,
            root_path=workspace_root,
            can_create_commands=_workspace_can_create_commands(workspace),
            commands=tuple(
                command_detail_from_definition(command)
                for command in self.registry.list_project_commands(
                    workspace_root=workspace_root
                )
            ),
        )

    def _resolve_global_target(self, relative_path: PurePosixPath) -> Path:
        base_dir = (self.registry.app_config_dir / "commands").resolve()
        return _resolve_target_under_base(
            base_dir=base_dir, relative_path=relative_path
        )

    @staticmethod
    def _resolve_project_target(
        *,
        workspace: WorkspaceRecord,
        source: CommandCreateSource,
        relative_path: PurePosixPath,
    ) -> Path:
        mount = _local_command_mount(workspace)
        if mount is None:
            raise ValueError("Command creation requires a local workspace root")
        if mount.capabilities is not None and not mount.capabilities.can_write:
            raise ValueError("Workspace command mount is not writable")
        root_path = mount.local_root_path()
        if root_path is None:
            raise ValueError("Command creation requires a local workspace root")
        base_dir = (_source_base_dir(root_path=root_path, source=source)).resolve()
        target_path = _resolve_target_under_base(
            base_dir=base_dir,
            relative_path=relative_path,
        )
        if not _mount_allows_target_path(
            root_path=root_path,
            mount=mount,
            target_path=target_path,
        ):
            raise ValueError("Command target is outside the workspace writable scope")
        return target_path

    def _resolve_create_workspace(self, workspace_id: Optional[str]) -> WorkspaceRecord:
        safe_workspace_id = str(workspace_id or "").strip()
        if not safe_workspace_id:
            raise ValueError("Project command creation requires workspace_id")
        return self.workspace_service.get_workspace(safe_workspace_id)

    def _resolve_update_target(self, source_path: Path) -> _CommandUpdateTarget:
        target_path = source_path.expanduser().resolve()
        if target_path.suffix != ".md":
            raise ValueError("Command source path must be a .md file")
        if not target_path.is_file():
            raise FileNotFoundError(f"Command file does not exist: {target_path}")

        app_base_dir = (self.registry.app_config_dir / "commands").resolve()
        if _is_path_under_base(path=target_path, base_dir=app_base_dir):
            _ensure_discoverable_path_depth(path=target_path, base_dir=app_base_dir)
            return _CommandUpdateTarget(
                target_path=target_path,
                workspace_id=None,
                workspace_root=None,
            )

        for workspace in self.workspace_service.list_workspaces():
            target = _workspace_update_target(
                workspace=workspace,
                target_path=target_path,
            )
            if target is not None:
                return target
        raise ValueError("Command source path is outside supported command directories")

    def _find_updated_command(
        self,
        *,
        target_path: Path,
        workspace_root: Optional[Path],
    ) -> Optional[CommandDefinition]:
        return self.registry.get_discovered_command_by_source_path(
            source_path=target_path,
            workspace_root=workspace_root,
        )


class _CommandUpdateTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_path: Path
    workspace_id: Optional[str]
    workspace_root: Optional[Path]


def _normalize_command_name(name: str) -> str:
    safe_name = name.strip()
    if not is_valid_command_name(safe_name):
        raise ValueError(f"Invalid command name: {name}")
    return safe_name


def _normalize_allowed_modes(value: tuple[str, ...]) -> tuple[str, ...]:
    modes = tuple(item.strip() for item in value if item.strip())
    return modes or ("normal",)


def _normalize_aliases(value: tuple[str, ...], *, command_name: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for item in value:
        alias = item.strip().removeprefix("/").strip()
        if not alias:
            continue
        if not is_valid_command_name(alias):
            raise ValueError(f"Invalid command alias: {item}")
        if alias == command_name or alias in aliases:
            continue
        aliases.append(alias)
    return tuple(aliases)


def _normalize_relative_markdown_path(value: str) -> PurePosixPath:
    normalized = value.strip().replace("\\", "/")
    relative_path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or relative_path.is_absolute()
        or any(part in {"", ".", ".."} for part in relative_path.parts)
        or relative_path.suffix != ".md"
        or len(relative_path.parts) > DEFAULT_COMMAND_MAX_DEPTH + 1
    ):
        raise ValueError("Command path must be a relative .md path")
    return relative_path


def _resolve_target_under_base(
    *,
    base_dir: Path,
    relative_path: PurePosixPath,
) -> Path:
    target_path = (base_dir / Path(*relative_path.parts)).resolve()
    if target_path == base_dir or base_dir not in target_path.parents:
        raise ValueError("Command path escapes the command directory")
    return target_path


def _is_path_under_base(*, path: Path, base_dir: Path) -> bool:
    return path == base_dir or base_dir in path.parents


def _ensure_discoverable_path_depth(*, path: Path, base_dir: Path) -> None:
    relative_path = path.relative_to(base_dir)
    if len(relative_path.parts) > DEFAULT_COMMAND_MAX_DEPTH + 1:
        raise ValueError("Command path exceeds discovery depth")


def _source_base_dir(*, root_path: Path, source: CommandCreateSource) -> Path:
    if source == CommandCreateSource.CLAUDE:
        return root_path / ".claude" / "commands"
    if source == CommandCreateSource.CODEX:
        return root_path / ".codex" / "commands"
    if source == CommandCreateSource.OPENCODE:
        return root_path / ".opencode" / "commands"
    return root_path / ".relay-teams" / "commands"


def _workspace_command_base_dirs(root_path: Path) -> tuple[Path, ...]:
    root = root_path.resolve()
    return (
        (root / ".relay-teams" / "commands").resolve(),
        (root / ".claude" / "commands").resolve(),
        (root / ".codex" / "commands").resolve(),
        (root / ".opencode" / "command").resolve(),
        (root / ".opencode" / "commands").resolve(),
    )


def _local_command_mount(workspace: WorkspaceRecord) -> Optional[WorkspaceMountRecord]:
    default_root = workspace.default_mount.local_root_path()
    if default_root is not None:
        return workspace.default_mount
    return workspace.first_local_mount()


def _workspace_can_create_commands(workspace: WorkspaceRecord) -> bool:
    mount = _local_command_mount(workspace)
    if mount is None:
        return False
    if mount.capabilities is not None and not mount.capabilities.can_write:
        return False
    root_path = mount.local_root_path()
    if root_path is None:
        return False
    writable_roots = _mount_writable_root_paths(root_path=root_path, mount=mount)
    command_base_dirs = tuple(
        _source_base_dir(root_path=root_path, source=source).resolve()
        for source in CommandCreateSource
    )
    return any(
        _path_roots_overlap(command_base_dir, writable_root)
        for command_base_dir in command_base_dirs
        for writable_root in writable_roots
    )


def _path_roots_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _mount_allows_target_path(
    *,
    root_path: Path,
    mount: WorkspaceMountRecord,
    target_path: Path,
) -> bool:
    allowed_roots = _mount_writable_root_paths(root_path=root_path, mount=mount)
    return _is_within_any_root(target_path, allowed_roots)


def _mount_writable_root_paths(
    *,
    root_path: Path,
    mount: WorkspaceMountRecord,
) -> tuple[Path, ...]:
    return tuple(
        (root_path / writable_path).resolve() for writable_path in mount.writable_paths
    )


def _workspace_update_target(
    *,
    workspace: WorkspaceRecord,
    target_path: Path,
) -> Optional[_CommandUpdateTarget]:
    workspace_root = workspace.root_path
    if workspace_root is None:
        return None
    owning_base_dir = _first_path_base(
        path=target_path,
        base_dirs=_workspace_command_base_dirs(workspace_root),
    )
    if owning_base_dir is None:
        return None
    _ensure_discoverable_path_depth(path=target_path, base_dir=owning_base_dir)

    mount = _local_mount_owning_path(workspace=workspace, target_path=target_path)
    if mount is None:
        return None
    if mount.capabilities is not None and not mount.capabilities.can_write:
        raise ValueError("Workspace local mount is not writable")
    root_path = mount.local_root_path()
    if root_path is None:
        return None
    if not _mount_allows_target_path(
        root_path=root_path,
        mount=mount,
        target_path=target_path,
    ):
        raise ValueError("Command source path is outside the workspace writable scope")
    return _CommandUpdateTarget(
        target_path=target_path,
        workspace_id=workspace.workspace_id,
        workspace_root=workspace_root,
    )


def _local_mount_owning_path(
    *,
    workspace: WorkspaceRecord,
    target_path: Path,
) -> Optional[WorkspaceMountRecord]:
    best_mount: Optional[WorkspaceMountRecord] = None
    best_root: Optional[Path] = None
    for mount in workspace.mounts:
        root_path = mount.local_root_path()
        if root_path is None:
            continue
        root = root_path.resolve()
        if target_path != root and root not in target_path.parents:
            continue
        if best_root is None or len(root.parts) > len(best_root.parts):
            best_mount = mount
            best_root = root
    return best_mount


def _is_within_any_root(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _first_path_base(*, path: Path, base_dirs: tuple[Path, ...]) -> Optional[Path]:
    for base_dir in base_dirs:
        if _is_path_under_base(path=path, base_dir=base_dir):
            return base_dir
    return None


def _render_command_markdown(
    *,
    name: str,
    aliases: tuple[str, ...],
    description: str,
    argument_hint: str,
    allowed_modes: tuple[str, ...],
    template: str,
) -> str:
    front_matter = {
        "name": name,
        "description": description.strip(),
        "argument_hint": argument_hint.strip(),
        "allowed_modes": list(allowed_modes),
    }
    if aliases:
        front_matter["aliases"] = list(aliases)
    front_matter_text = yaml.safe_dump(
        front_matter,
        allow_unicode=True,
        sort_keys=False,
    ).strip()
    return f"---\n{front_matter_text}\n---\n{template.strip()}\n"
