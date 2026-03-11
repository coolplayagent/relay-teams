# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.workspace.handle import WorkspaceHandle
from agent_teams.workspace.ids import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
    build_instance_workspace_id,
    build_workspace_id,
)
from agent_teams.workspace.manager import (
    WorkspaceManager,
)
from agent_teams.workspace.models import (
    BranchBinding,
    FileScopeBackend,
    StateScope,
    WorkspaceBackend,
    WorkspaceBinding,
    WorkspaceCapability,
    WorkspaceFileScope,
    WorkspaceLocations,
    WorkspaceProfile,
    WorkspaceRef,
    default_workspace_profile,
    ensure_instance_workspace_profile,
)

__all__ = [
    "WorkspaceBackend",
    "WorkspaceBinding",
    "WorkspaceCapability",
    "WorkspaceHandle",
    "WorkspaceLocations",
    "WorkspaceManager",
    "WorkspaceProfile",
    "WorkspaceRef",
    "WorkspaceFileScope",
    "StateScope",
    "BranchBinding",
    "FileScopeBackend",
    "build_conversation_id",
    "build_instance_conversation_id",
    "build_instance_role_scope_id",
    "build_instance_session_scope_id",
    "build_instance_workspace_id",
    "build_workspace_id",
    "default_workspace_profile",
    "ensure_instance_workspace_profile",
]
