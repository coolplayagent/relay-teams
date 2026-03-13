# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.workspace.handle import WorkspaceHandle
from agent_teams.workspace.ids import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)
from agent_teams.workspace.directory_picker import pick_workspace_directory
from agent_teams.workspace.manager import (
    WorkspaceManager,
)
from agent_teams.workspace.models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceBackend,
    WorkspaceFileScope,
    WorkspaceLocations,
    WorkspaceProfile,
    WorkspaceRecord,
    WorkspaceRef,
    default_workspace_profile,
)
from agent_teams.workspace.repository import WorkspaceRepository
from agent_teams.workspace.service import WorkspaceService

__all__ = [
    "WorkspaceBackend",
    "WorkspaceHandle",
    "WorkspaceLocations",
    "WorkspaceManager",
    "WorkspaceProfile",
    "WorkspaceRecord",
    "WorkspaceRepository",
    "WorkspaceRef",
    "WorkspaceService",
    "WorkspaceFileScope",
    "BranchBinding",
    "FileScopeBackend",
    "build_conversation_id",
    "build_instance_conversation_id",
    "build_instance_role_scope_id",
    "build_instance_session_scope_id",
    "default_workspace_profile",
    "pick_workspace_directory",
]
