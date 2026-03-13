# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.workspace.models import (
    WorkspaceProfile,
    WorkspaceRecord,
)
from agent_teams.workspace.repository import WorkspaceRepository


class WorkspaceService:
    def __init__(self, *, repository: WorkspaceRepository) -> None:
        self._repository = repository

    def create_workspace(
        self,
        *,
        workspace_id: str,
        root_path: Path,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        resolved_root = root_path.resolve()
        if not resolved_root.exists():
            raise ValueError(f"Workspace root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise ValueError(f"Workspace root is not a directory: {resolved_root}")
        if self._repository.exists(workspace_id):
            raise ValueError(f"Workspace already exists: {workspace_id}")
        return self._repository.create(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._repository.get(workspace_id)

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return self._repository.list_all()

    def require_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self.get_workspace(workspace_id)
