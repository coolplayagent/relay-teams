# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.workspace.directory_picker import pick_workspace_directory
from agent_teams.workspace.git_worktree import GitWorktreeClient
from agent_teams.workspace.handle import WorkspaceHandle
from agent_teams.workspace.ids import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)
from agent_teams.workspace.workspace_manager import WorkspaceManager
from agent_teams.workspace.workspace_models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceBackend,
    WorkspaceDiffChangeType,
    WorkspaceDiffFile,
    WorkspaceDiffFileSummary,
    WorkspaceDiffListing,
    WorkspaceFileScope,
    WorkspaceLocations,
    WorkspaceProfile,
    WorkspaceRecord,
    WorkspaceRef,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    WorkspaceTreeNode,
    WorkspaceTreeNodeKind,
    default_workspace_profile,
)
from agent_teams.workspace.workspace_repository import WorkspaceRepository
from agent_teams.workspace.workspace_service import WorkspaceService

__all__ = [
    "WorkspaceBackend",
    "WorkspaceHandle",
    "GitWorktreeClient",
    "WorkspaceDiffChangeType",
    "WorkspaceDiffFile",
    "WorkspaceDiffFileSummary",
    "WorkspaceDiffListing",
    "WorkspaceLocations",
    "WorkspaceManager",
    "WorkspaceProfile",
    "WorkspaceRecord",
    "WorkspaceRepository",
    "WorkspaceRef",
    "WorkspaceSnapshot",
    "WorkspaceTreeListing",
    "WorkspaceService",
    "WorkspaceFileScope",
    "WorkspaceTreeNode",
    "WorkspaceTreeNodeKind",
    "BranchBinding",
    "FileScopeBackend",
    "build_conversation_id",
    "build_instance_conversation_id",
    "build_instance_role_scope_id",
    "build_instance_session_scope_id",
    "default_workspace_profile",
    "pick_workspace_directory",
]
