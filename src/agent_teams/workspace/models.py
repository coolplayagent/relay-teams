# -*- coding: utf-8 -*-
from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceBinding(str, Enum):
    SESSION = "session"
    ROLE = "role"
    INSTANCE = "instance"
    TASK = "task"


class WorkspaceBackend(str, Enum):
    FILESYSTEM = "filesystem"
    SQLITE = "sqlite"
    HYBRID = "hybrid"


class WorkspaceCapability(str, Enum):
    FILES = "files"
    SHELL = "shell"
    HISTORY = "history"
    MEMORY = "memory"
    ARTIFACTS = "artifacts"


class FileScopeBackend(str, Enum):
    PROJECT = "project"
    GIT_WORKTREE = "git_worktree"


class BranchBinding(str, Enum):
    SHARED = "shared"
    SESSION = "session"
    ROLE = "role"
    INSTANCE = "instance"


class StateScope(str, Enum):
    WORKSPACE = "workspace"
    SESSION = "session"
    ROLE = "role"
    CONVERSATION = "conversation"
    TASK = "task"
    INSTANCE = "instance"


def default_state_scopes() -> tuple[StateScope, ...]:
    return (
        StateScope.WORKSPACE,
        StateScope.SESSION,
        StateScope.ROLE,
        StateScope.CONVERSATION,
    )


class WorkspaceFileScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: FileScopeBackend = FileScopeBackend.PROJECT
    working_directory: str = "."
    readable_paths: tuple[str, ...] = (".",)
    writable_paths: tuple[str, ...] = (".",)
    branch_binding: BranchBinding = BranchBinding.SHARED
    branch_name: str | None = None


def default_workspace_profile() -> WorkspaceProfile:
    return WorkspaceProfile(
        binding=WorkspaceBinding.SESSION,
        backend=WorkspaceBackend.HYBRID,
        capabilities=(
            WorkspaceCapability.HISTORY,
            WorkspaceCapability.MEMORY,
            WorkspaceCapability.ARTIFACTS,
        ),
        readable_scopes=default_state_scopes(),
        writable_scopes=(
            StateScope.ROLE,
            StateScope.CONVERSATION,
        ),
        persistent_scopes=(
            StateScope.ROLE,
            StateScope.CONVERSATION,
        ),
        file_scope=WorkspaceFileScope(),
    )


def ensure_instance_workspace_profile(
    profile: WorkspaceProfile,
) -> WorkspaceProfile:
    if profile.binding == WorkspaceBinding.INSTANCE:
        return profile
    return profile.model_copy(update={"binding": WorkspaceBinding.INSTANCE})


class WorkspaceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: WorkspaceBinding = WorkspaceBinding.SESSION
    backend: WorkspaceBackend = WorkspaceBackend.HYBRID
    capabilities: tuple[WorkspaceCapability, ...] = Field(
        default_factory=lambda: default_workspace_profile().capabilities
    )
    readable_scopes: tuple[StateScope, ...] = Field(
        default_factory=default_state_scopes
    )
    writable_scopes: tuple[StateScope, ...] = Field(
        default_factory=lambda: default_workspace_profile().writable_scopes
    )
    persistent_scopes: tuple[StateScope, ...] = Field(
        default_factory=lambda: default_workspace_profile().persistent_scopes
    )
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

    project_root: Path
    config_dir: Path
    workspace_dir: Path
    execution_root: Path
    readable_roots: tuple[Path, ...]
    writable_roots: tuple[Path, ...]
    worktree_root: Path | None = None
    branch_name: str | None = None
