# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class WorkspaceBackend(str, Enum):
    FILESYSTEM = "filesystem"


class FileScopeBackend(str, Enum):
    PROJECT = "project"
    GIT_WORKTREE = "git_worktree"


class BranchBinding(str, Enum):
    SHARED = "shared"
    SESSION = "session"
    ROLE = "role"
    INSTANCE = "instance"


class WorkspaceFileScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: FileScopeBackend = FileScopeBackend.PROJECT
    working_directory: str = "."
    readable_paths: tuple[str, ...] = (".",)
    writable_paths: tuple[str, ...] = (".",)
    branch_binding: BranchBinding = BranchBinding.SHARED
    branch_name: str | None = None
    source_root_path: str | None = None
    forked_from_workspace_id: OptionalIdentifierStr = None


class WorkspaceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: WorkspaceBackend = WorkspaceBackend.FILESYSTEM
    file_scope: WorkspaceFileScope = Field(default_factory=WorkspaceFileScope)


def default_workspace_profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        backend=WorkspaceBackend.FILESYSTEM,
        file_scope=WorkspaceFileScope(),
    )


class WorkspaceMountProvider(str, Enum):
    LOCAL = "local"
    SSH = "ssh"


class WorkspaceMountCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_read: bool = True
    can_write: bool = True
    can_search: bool = True
    can_shell: bool = True
    can_diff: bool = True
    can_preview: bool = True


def default_mount_capabilities(
    provider: WorkspaceMountProvider,
) -> WorkspaceMountCapabilities:
    if provider == WorkspaceMountProvider.SSH:
        return WorkspaceMountCapabilities(
            can_read=True,
            can_write=True,
            can_search=True,
            can_shell=True,
            can_diff=False,
            can_preview=False,
        )
    return WorkspaceMountCapabilities()


class WorkspaceLocalMountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_path: Path


class WorkspaceSshMountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssh_profile_id: RequiredIdentifierStr
    remote_root: str = Field(min_length=1)

    @field_validator("remote_root")
    @classmethod
    def _normalize_remote_root(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("remote_root must not be empty")
        return normalized


class WorkspaceMountRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mount_name: RequiredIdentifierStr
    provider: WorkspaceMountProvider
    provider_config: WorkspaceLocalMountConfig | WorkspaceSshMountConfig
    working_directory: str = "."
    readable_paths: tuple[str, ...] = (".",)
    writable_paths: tuple[str, ...] = (".",)
    capabilities: WorkspaceMountCapabilities | None = None
    branch_name: str | None = None
    source_root_path: str | None = None
    forked_from_workspace_id: OptionalIdentifierStr = None

    @model_validator(mode="after")
    def _validate_provider_config(self) -> WorkspaceMountRecord:
        if self.provider == WorkspaceMountProvider.LOCAL and not isinstance(
            self.provider_config, WorkspaceLocalMountConfig
        ):
            raise ValueError("local mount requires WorkspaceLocalMountConfig")
        if self.provider == WorkspaceMountProvider.SSH and not isinstance(
            self.provider_config, WorkspaceSshMountConfig
        ):
            raise ValueError("ssh mount requires WorkspaceSshMountConfig")
        if self.capabilities is None:
            self.capabilities = default_mount_capabilities(self.provider)
        return self

    @property
    def root_reference(self) -> str:
        if isinstance(self.provider_config, WorkspaceLocalMountConfig):
            return str(self.provider_config.root_path.resolve())
        return self.provider_config.remote_root

    def local_root_path(self) -> Path | None:
        if not isinstance(self.provider_config, WorkspaceLocalMountConfig):
            return None
        return self.provider_config.root_path.resolve()


def build_local_workspace_mount(
    *,
    mount_name: str,
    root_path: Path,
    working_directory: str = ".",
    readable_paths: tuple[str, ...] = (".",),
    writable_paths: tuple[str, ...] = (".",),
    branch_name: str | None = None,
    source_root_path: str | None = None,
    forked_from_workspace_id: str | None = None,
) -> WorkspaceMountRecord:
    return WorkspaceMountRecord(
        mount_name=mount_name,
        provider=WorkspaceMountProvider.LOCAL,
        provider_config=WorkspaceLocalMountConfig(root_path=root_path.resolve()),
        working_directory=working_directory,
        readable_paths=readable_paths,
        writable_paths=writable_paths,
        branch_name=branch_name,
        source_root_path=source_root_path,
        forked_from_workspace_id=forked_from_workspace_id,
    )


def legacy_workspace_mount_from_profile(
    *,
    root_path: Path,
    profile: WorkspaceProfile,
    mount_name: str = "default",
) -> WorkspaceMountRecord:
    file_scope = profile.file_scope
    return build_local_workspace_mount(
        mount_name=mount_name,
        root_path=root_path,
        working_directory=file_scope.working_directory,
        readable_paths=file_scope.readable_paths,
        writable_paths=file_scope.writable_paths,
        branch_name=file_scope.branch_name,
        source_root_path=file_scope.source_root_path,
        forked_from_workspace_id=file_scope.forked_from_workspace_id,
    )


def legacy_workspace_profile_from_mount(
    mount: WorkspaceMountRecord,
) -> WorkspaceProfile:
    backend = (
        FileScopeBackend.GIT_WORKTREE
        if mount.source_root_path is not None
        else FileScopeBackend.PROJECT
    )
    return WorkspaceProfile(
        backend=WorkspaceBackend.FILESYSTEM,
        file_scope=WorkspaceFileScope(
            backend=backend,
            working_directory=mount.working_directory,
            readable_paths=mount.readable_paths,
            writable_paths=mount.writable_paths,
            branch_name=mount.branch_name,
            source_root_path=mount.source_root_path,
            forked_from_workspace_id=mount.forked_from_workspace_id,
        ),
    )


class WorkspaceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    conversation_id: RequiredIdentifierStr
    default_mount_name: RequiredIdentifierStr
    mount_names: tuple[str, ...] = ()
    instance_id: OptionalIdentifierStr = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        coerced = dict(data)
        coerced.pop("profile", None)
        coerced.setdefault("default_mount_name", "default")
        coerced.setdefault("mount_names", ("default",))
        return coerced


class WorkspaceLocations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_dir: Path
    mount_name: RequiredIdentifierStr = "default"
    provider: WorkspaceMountProvider = WorkspaceMountProvider.LOCAL
    scope_root: Path
    execution_root: Path
    tmp_root: Path
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    worktree_root: Path | None = None
    branch_name: str | None = None


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    default_mount_name: RequiredIdentifierStr
    mounts: tuple[WorkspaceMountRecord, ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_root_path(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "mounts" in data or "root_path" not in data:
            return data
        raw_root_path = data.get("root_path")
        if raw_root_path is None:
            return data
        raw_profile = data.get("profile")
        profile = (
            raw_profile
            if isinstance(raw_profile, WorkspaceProfile)
            else WorkspaceProfile.model_validate(raw_profile)
            if isinstance(raw_profile, dict)
            else default_workspace_profile()
        )
        mount_name = str(data.get("default_mount_name") or "default")
        mounts = (
            legacy_workspace_mount_from_profile(
                root_path=Path(str(raw_root_path)).resolve(),
                profile=profile,
                mount_name=mount_name,
            ),
        )
        coerced = dict(data)
        coerced.pop("root_path", None)
        coerced.pop("profile", None)
        coerced.setdefault("default_mount_name", mount_name)
        coerced["mounts"] = mounts
        return coerced

    @model_validator(mode="after")
    def _validate_mounts(self) -> WorkspaceRecord:
        if len(self.mounts) == 0:
            raise ValueError("workspace must include at least one mount")
        seen: set[str] = set()
        for mount in self.mounts:
            if mount.mount_name in seen:
                raise ValueError(f"duplicate workspace mount: {mount.mount_name}")
            seen.add(mount.mount_name)
        if self.default_mount_name not in seen:
            raise ValueError(f"default mount does not exist: {self.default_mount_name}")
        return self

    def mount_by_name(self, mount_name: str) -> WorkspaceMountRecord:
        for mount in self.mounts:
            if mount.mount_name == mount_name:
                return mount
        raise KeyError(f"Unknown workspace mount: {mount_name}")

    @property
    def default_mount(self) -> WorkspaceMountRecord:
        return self.mount_by_name(self.default_mount_name)

    def first_local_mount(self) -> WorkspaceMountRecord | None:
        for mount in self.mounts:
            if mount.provider == WorkspaceMountProvider.LOCAL:
                return mount
        return None

    @computed_field(return_type=WorkspaceProfile)
    @property
    def profile(self) -> WorkspaceProfile:
        local_mount = self.first_local_mount()
        target_mount = self.default_mount if local_mount is None else local_mount
        return legacy_workspace_profile_from_mount(target_mount)

    @computed_field(return_type=Path | None)
    @property
    def root_path(self) -> Path | None:
        default_root = self.default_mount.local_root_path()
        if default_root is not None:
            return default_root
        first_local_mount = self.first_local_mount()
        if first_local_mount is not None:
            first_local_root = first_local_mount.local_root_path()
            if first_local_root is not None:
                return first_local_root
        return None


class WorkspaceTreeNodeKind(str, Enum):
    DIRECTORY = "directory"
    FILE = "file"


class WorkspaceDiffChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    UNTRACKED = "untracked"
    CONFLICTED = "conflicted"
    TYPE_CHANGED = "type_changed"


class WorkspaceTreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    path: str
    kind: WorkspaceTreeNodeKind
    has_children: bool = False
    children: tuple[WorkspaceTreeNode, ...] = ()


class WorkspaceTreeListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    mount_name: RequiredIdentifierStr = "default"
    directory_path: str
    children: tuple[WorkspaceTreeNode, ...] = ()


class WorkspaceDiffFileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    change_type: WorkspaceDiffChangeType
    previous_path: str | None = None


class WorkspaceDiffFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mount_name: RequiredIdentifierStr = "default"
    path: str = Field(min_length=1)
    change_type: WorkspaceDiffChangeType
    previous_path: str | None = None
    diff: str = ""
    is_binary: bool = False


class WorkspaceDiffListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    mount_name: RequiredIdentifierStr = "default"
    root_path: Path | str
    diff_files: tuple[WorkspaceDiffFileSummary, ...] = ()
    is_git_repository: bool = False
    git_root_path: Path | str | None = None
    diff_message: str | None = None


class WorkspaceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    default_mount_name: RequiredIdentifierStr = "default"
    default_mount_root: Path | None = None
    tree: WorkspaceTreeNode

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_root_path(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "default_mount_root" in data or "root_path" not in data:
            return data
        coerced = dict(data)
        coerced["default_mount_root"] = data.get("root_path")
        coerced.pop("root_path", None)
        coerced.setdefault("default_mount_name", "default")
        return coerced

    @computed_field(return_type=Path | None)
    @property
    def root_path(self) -> Path | None:
        return self.default_mount_root
