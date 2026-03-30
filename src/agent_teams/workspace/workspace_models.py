# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


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


def default_workspace_profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        backend=WorkspaceBackend.FILESYSTEM,
        file_scope=WorkspaceFileScope(),
    )


class WorkspaceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: WorkspaceBackend = WorkspaceBackend.FILESYSTEM
    file_scope: WorkspaceFileScope = Field(default_factory=WorkspaceFileScope)


class WorkspaceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    conversation_id: RequiredIdentifierStr
    instance_id: OptionalIdentifierStr = None
    profile: WorkspaceProfile = Field(default_factory=default_workspace_profile)


class WorkspaceLocations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_dir: Path
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
    root_path: Path
    profile: WorkspaceProfile = Field(default_factory=default_workspace_profile)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


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
    directory_path: str
    children: tuple[WorkspaceTreeNode, ...] = ()


class WorkspaceDiffFileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    change_type: WorkspaceDiffChangeType
    previous_path: str | None = None


class WorkspaceDiffFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    change_type: WorkspaceDiffChangeType
    previous_path: str | None = None
    diff: str = ""
    is_binary: bool = False


class WorkspaceDiffListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    root_path: Path
    diff_files: tuple[WorkspaceDiffFileSummary, ...] = ()
    is_git_repository: bool = False
    git_root_path: Path | None = None
    diff_message: str | None = None


class WorkspaceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: RequiredIdentifierStr
    root_path: Path
    tree: WorkspaceTreeNode
