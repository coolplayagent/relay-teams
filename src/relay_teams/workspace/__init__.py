# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.workspace.directory_picker import pick_workspace_directory
from relay_teams.workspace.git_worktree import GitWorktreeClient
from relay_teams.workspace.handle import WorkspaceHandle
from relay_teams.workspace.ssh_profile_secret_store import (
    SshProfileSecretStore,
    get_ssh_profile_secret_store,
)
from relay_teams.workspace.ids import (
    build_conversation_id,
    build_instance_conversation_id,
    build_instance_role_scope_id,
    build_instance_session_scope_id,
)
from relay_teams.workspace.ssh_profile_models import (
    SshProfileConfig,
    SshProfileConnectivityDiagnostics,
    SshProfileConnectivityProbeRequest,
    SshProfileConnectivityProbeResult,
    SshProfilePasswordRevealView,
    SshProfileRecord,
    SshProfileStoredConfig,
)
from relay_teams.workspace.ssh_profile_repository import SshProfileRepository
from relay_teams.workspace.ssh_profile_service import SshProfileService
from relay_teams.workspace.workspace_manager import WorkspaceManager
from relay_teams.workspace.workspace_models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceBackend,
    WorkspaceDiffChangeType,
    WorkspaceDiffFile,
    WorkspaceDiffFileSummary,
    WorkspaceDiffListing,
    WorkspaceFileScope,
    WorkspaceLocalMountConfig,
    WorkspaceLocations,
    WorkspaceMountCapabilities,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceProfile,
    WorkspaceRecord,
    WorkspaceRef,
    WorkspaceSshMountConfig,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    WorkspaceTreeNode,
    WorkspaceTreeNodeKind,
    build_local_workspace_mount,
    default_workspace_profile,
    legacy_workspace_mount_from_profile,
    legacy_workspace_profile_from_mount,
)
from relay_teams.workspace.workspace_repository import WorkspaceRepository
from relay_teams.workspace.workspace_service import WorkspaceService

__all__ = [
    "WorkspaceBackend",
    "WorkspaceHandle",
    "GitWorktreeClient",
    "WorkspaceDiffChangeType",
    "WorkspaceDiffFile",
    "WorkspaceDiffFileSummary",
    "WorkspaceDiffListing",
    "WorkspaceLocations",
    "WorkspaceLocalMountConfig",
    "WorkspaceManager",
    "WorkspaceMountCapabilities",
    "WorkspaceMountProvider",
    "WorkspaceMountRecord",
    "WorkspaceProfile",
    "WorkspaceRecord",
    "WorkspaceRepository",
    "WorkspaceRef",
    "WorkspaceSshMountConfig",
    "WorkspaceSnapshot",
    "WorkspaceTreeListing",
    "WorkspaceService",
    "WorkspaceFileScope",
    "WorkspaceTreeNode",
    "WorkspaceTreeNodeKind",
    "SshProfileConfig",
    "SshProfileConnectivityDiagnostics",
    "SshProfileConnectivityProbeRequest",
    "SshProfileConnectivityProbeResult",
    "SshProfilePasswordRevealView",
    "SshProfileRecord",
    "SshProfileStoredConfig",
    "SshProfileRepository",
    "SshProfileService",
    "SshProfileSecretStore",
    "BranchBinding",
    "FileScopeBackend",
    "build_local_workspace_mount",
    "build_conversation_id",
    "build_instance_conversation_id",
    "build_instance_role_scope_id",
    "build_instance_session_scope_id",
    "default_workspace_profile",
    "legacy_workspace_mount_from_profile",
    "legacy_workspace_profile_from_mount",
    "pick_workspace_directory",
    "get_ssh_profile_secret_store",
]
