# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

from agent_teams.workspace.models import (
    WorkspaceProfile,
    WorkspaceRecord,
)
from agent_teams.workspace.repository import WorkspaceRepository


_NON_WORKSPACE_ID_CHARS = re.compile(r"[^a-z0-9]+")


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
        resolved_root = self._validate_root(root_path)
        if self._repository.exists(workspace_id):
            raise ValueError(f"Workspace already exists: {workspace_id}")
        return self._repository.create(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

    def create_workspace_for_root(
        self,
        *,
        root_path: Path,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        resolved_root = self._validate_root(root_path)
        existing = self._find_workspace_by_root(resolved_root)
        if existing is not None:
            return existing

        workspace_id = self._next_workspace_id_for_root(resolved_root)
        return self._repository.create(
            workspace_id=workspace_id,
            root_path=resolved_root,
            profile=profile,
        )

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self._repository.get(workspace_id)

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        return self._repository.list_all()

    def delete_workspace(self, workspace_id: str) -> None:
        _ = self._repository.get(workspace_id)
        self._repository.delete(workspace_id)

    def require_workspace(self, workspace_id: str) -> WorkspaceRecord:
        return self.get_workspace(workspace_id)

    def _validate_root(self, root_path: Path) -> Path:
        resolved_root = root_path.resolve()
        if not resolved_root.exists():
            raise ValueError(f"Workspace root does not exist: {resolved_root}")
        if not resolved_root.is_dir():
            raise ValueError(f"Workspace root is not a directory: {resolved_root}")
        return resolved_root

    def _find_workspace_by_root(self, root_path: Path) -> WorkspaceRecord | None:
        for workspace in self._repository.list_all():
            if workspace.root_path == root_path:
                return workspace
        return None

    def _next_workspace_id_for_root(self, root_path: Path) -> str:
        base_name = root_path.name.strip() or "project"
        base_id = _NON_WORKSPACE_ID_CHARS.sub("-", base_name.lower()).strip("-")
        normalized_base = base_id or "project"
        existing_ids = {
            workspace.workspace_id for workspace in self._repository.list_all()
        }
        if normalized_base not in existing_ids:
            return normalized_base

        suffix = 2
        while True:
            candidate = f"{normalized_base}-{suffix}"
            if candidate not in existing_ids:
                return candidate
            suffix += 1
