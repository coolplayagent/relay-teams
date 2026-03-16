# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


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
    forked_from_workspace_id: str | None = None


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

    workspace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    instance_id: str | None = None
    profile: WorkspaceProfile = Field(default_factory=default_workspace_profile)


class WorkspaceLocations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_dir: Path
    execution_root: Path
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    worktree_root: Path | None = None
    branch_name: str | None = None


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    root_path: Path
    profile: WorkspaceProfile = Field(default_factory=default_workspace_profile)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
