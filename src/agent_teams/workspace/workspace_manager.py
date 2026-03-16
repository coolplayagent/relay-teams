# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.paths import get_project_config_dir
from agent_teams.workspace.handle import WorkspaceHandle
from agent_teams.workspace.ids import build_conversation_id
from agent_teams.workspace.workspace_models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceRecord,
    WorkspaceLocations,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRef,
    default_workspace_profile,
)
from agent_teams.workspace.workspace_repository import WorkspaceRepository


class WorkspaceManager(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_root: Path
    workspace_repo: WorkspaceRepository | None = None
    shared_store: object | None = None

    def resolve(
        self,
        *,
        session_id: str,
        role_id: str,
        instance_id: str | None,
        profile: WorkspaceProfile | None = None,
        workspace_id: str,
        conversation_id: str | None = None,
    ) -> WorkspaceHandle:
        resolved_conversation_id = conversation_id or build_conversation_id(
            session_id, role_id
        )
        record = self._resolve_record(workspace_id, profile)
        ref = WorkspaceRef(
            workspace_id=record.workspace_id,
            session_id=session_id,
            role_id=role_id,
            conversation_id=resolved_conversation_id,
            instance_id=instance_id,
            profile=record.profile,
        )
        locations = self._resolve_locations(
            record=record,
            profile=record.profile,
        )
        return WorkspaceHandle(
            ref=ref,
            profile=record.profile,
            locations=locations,
        )

    def locations_for(self, workspace_id: str) -> WorkspaceLocations:
        record = self._resolve_record(workspace_id, None)
        config_dir = get_project_config_dir(project_root=record.root_path)
        return WorkspaceLocations(
            workspace_dir=config_dir / "workspaces" / workspace_id,
            execution_root=record.root_path,
            readable_roots=(record.root_path,),
            writable_roots=(record.root_path,),
        )

    def delete_workspace(self, workspace_id: str) -> None:
        shutil.rmtree(
            self.locations_for(workspace_id).workspace_dir, ignore_errors=True
        )

    def session_artifact_dir(self, *, workspace_id: str, session_id: str) -> Path:
        record = self._resolve_record(workspace_id, None)
        filesystem_root = self._filesystem_root_for_record(record)
        return filesystem_root / ".agent_teams" / "sessions" / session_id

    def role_stage_dir(
        self,
        *,
        workspace_id: str,
        session_id: str,
        role_id: str,
        stage_name: str,
    ) -> Path:
        return (
            self.session_artifact_dir(workspace_id=workspace_id, session_id=session_id)
            / "roles"
            / role_id
            / "stage"
            / stage_name
        )

    def _resolve_locations(
        self,
        *,
        record: WorkspaceRecord,
        profile: WorkspaceProfile,
    ) -> WorkspaceLocations:
        base_locations = self.locations_for(record.workspace_id)
        file_scope = profile.file_scope
        worktree_root = (
            record.root_path
            if file_scope.backend == FileScopeBackend.GIT_WORKTREE
            else None
        )
        filesystem_root = worktree_root or record.root_path
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
                "branch_name": self._resolve_branch_name(
                    record.workspace_id, file_scope
                ),
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

    def _resolve_record(
        self,
        workspace_id: str,
        profile: WorkspaceProfile | None,
    ) -> WorkspaceRecord:
        if self.workspace_repo is not None and self.workspace_repo.exists(workspace_id):
            return self.workspace_repo.get(workspace_id)
        return WorkspaceRecord(
            workspace_id=workspace_id,
            root_path=self.project_root.resolve(),
            profile=profile or default_workspace_profile(),
        )

    def _filesystem_root_for_record(self, record: WorkspaceRecord) -> Path:
        if record.profile.file_scope.backend == FileScopeBackend.GIT_WORKTREE:
            return record.root_path
        return record.root_path
