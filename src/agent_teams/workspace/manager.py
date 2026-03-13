# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.paths import get_project_config_dir
from agent_teams.persistence.shared_state_repo import SharedStateRepository
from agent_teams.workspace.artifacts import WorkspaceArtifacts
from agent_teams.workspace.handle import WorkspaceHandle
from agent_teams.workspace.ids import build_conversation_id, build_workspace_id
from agent_teams.workspace.memory import WorkspaceMemory
from agent_teams.workspace.models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceLocations,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRef,
    default_workspace_profile,
)


class WorkspaceManager(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_root: Path
    shared_store: SharedStateRepository

    def resolve(
        self,
        *,
        session_id: str,
        role_id: str,
        instance_id: str | None,
        profile: WorkspaceProfile | None = None,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
    ) -> WorkspaceHandle:
        resolved_workspace_id = workspace_id or build_workspace_id(session_id)
        resolved_conversation_id = conversation_id or build_conversation_id(
            session_id, role_id
        )
        resolved_profile = profile or default_workspace_profile()
        ref = WorkspaceRef(
            workspace_id=resolved_workspace_id,
            session_id=session_id,
            role_id=role_id,
            conversation_id=resolved_conversation_id,
            instance_id=instance_id,
            profile=resolved_profile,
        )
        locations = self._resolve_locations(
            workspace_id=resolved_workspace_id,
            profile=resolved_profile,
        )
        memory = WorkspaceMemory(
            store=self.shared_store,
            ref=ref,
            profile=resolved_profile,
        )
        artifacts = WorkspaceArtifacts(ref=ref, locations=locations)
        return WorkspaceHandle(
            ref=ref,
            profile=resolved_profile,
            locations=locations,
            memory=memory,
            artifacts=artifacts,
        )

    def locations_for(self, workspace_id: str) -> WorkspaceLocations:
        config_dir = get_project_config_dir(project_root=self.project_root)
        return WorkspaceLocations(
            project_root=self.project_root,
            config_dir=config_dir,
            workspace_dir=config_dir / "workspaces" / workspace_id,
            execution_root=self.project_root,
            readable_roots=(self.project_root,),
            writable_roots=(self.project_root,),
        )

    def delete_workspace(self, workspace_id: str) -> None:
        shutil.rmtree(
            self.locations_for(workspace_id).workspace_dir, ignore_errors=True
        )

    def _resolve_locations(
        self,
        *,
        workspace_id: str,
        profile: WorkspaceProfile,
    ) -> WorkspaceLocations:
        base_locations = self.locations_for(workspace_id)
        file_scope = profile.file_scope
        worktree_root = (
            base_locations.workspace_dir / "worktree"
            if file_scope.backend == FileScopeBackend.GIT_WORKTREE
            else None
        )
        filesystem_root = worktree_root or self.project_root
        execution_root = self._resolve_relative_root(
            filesystem_root,
            file_scope.working_directory,
        )
        readable_roots = self._resolve_roots(filesystem_root, file_scope, write=False)
        writable_roots = self._resolve_roots(filesystem_root, file_scope, write=True)
        return base_locations.model_copy(
            update={
                "execution_root": execution_root,
                "readable_roots": readable_roots,
                "writable_roots": writable_roots,
                "worktree_root": worktree_root,
                "branch_name": self._resolve_branch_name(workspace_id, file_scope),
            }
        )

    def _resolve_roots(
        self,
        filesystem_root: Path,
        file_scope: WorkspaceFileScope,
        *,
        write: bool,
    ) -> tuple[Path, ...]:
        raw_paths = file_scope.writable_paths if write else file_scope.readable_paths
        return tuple(
            self._resolve_relative_root(filesystem_root, raw_path)
            for raw_path in raw_paths
        )

    def _resolve_relative_root(self, filesystem_root: Path, relative_path: str) -> Path:
        candidate = (filesystem_root / relative_path).resolve()
        resolved_root = filesystem_root.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise ValueError(
                f"Workspace file scope escapes filesystem root: {relative_path}"
            )
        return candidate

    def _resolve_branch_name(
        self,
        workspace_id: str,
        file_scope: WorkspaceFileScope,
    ) -> str | None:
        if file_scope.branch_name:
            return file_scope.branch_name
        if file_scope.branch_binding == BranchBinding.SHARED:
            return None
        return f"{file_scope.branch_binding.value}/{workspace_id}"
