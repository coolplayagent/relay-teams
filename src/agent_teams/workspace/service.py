# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shutil
from pathlib import Path

from agent_teams.paths import get_project_config_dir
from agent_teams.workspace.models import (
    BranchBinding,
    FileScopeBackend,
    WorkspaceFileScope,
    WorkspaceProfile,
    WorkspaceRecord,
)
from agent_teams.workspace.git_worktree import GitWorktreeClient
from agent_teams.workspace.repository import WorkspaceRepository


_NON_WORKSPACE_ID_CHARS = re.compile(r"[^a-z0-9]+")


class WorkspaceService:
    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        git_worktree_client: GitWorktreeClient | None = None,
    ) -> None:
        self._repository = repository
        self._git_worktree_client = git_worktree_client or GitWorktreeClient()

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
        _ = self.delete_workspace_with_options(
            workspace_id=workspace_id,
            remove_worktree=False,
        )

    def delete_workspace_with_options(
        self,
        *,
        workspace_id: str,
        remove_worktree: bool,
    ) -> WorkspaceRecord:
        record = self._repository.get(workspace_id)
        if (
            remove_worktree
            and record.profile.file_scope.backend == FileScopeBackend.GIT_WORKTREE
        ):
            repository_root = self._resolve_worktree_repository_root(record)
            self._git_worktree_client.remove_worktree(
                repository_root=repository_root,
                target_path=record.root_path,
            )
            self._git_worktree_client.prune(repository_root)
            shutil.rmtree(self._workspace_storage_dir(workspace_id), ignore_errors=True)
        self._repository.delete(workspace_id)
        return record

    def fork_workspace(
        self,
        *,
        source_workspace_id: str,
        name: str,
    ) -> WorkspaceRecord:
        source_record = self._repository.get(source_workspace_id)
        normalized_workspace_id = self._normalize_workspace_id(name)
        if self._repository.exists(normalized_workspace_id):
            raise ValueError(f"Workspace already exists: {normalized_workspace_id}")

        repository_root = self._git_worktree_client.ensure_repository(
            source_record.root_path
        )
        start_point = self._git_worktree_client.current_head(source_record.root_path)
        target_path = self._workspace_storage_dir(normalized_workspace_id) / "worktree"
        if target_path.exists():
            raise ValueError(f"Workspace root already exists: {target_path}")

        branch_name = f"fork/{normalized_workspace_id}"
        self._git_worktree_client.add_worktree(
            repository_root=source_record.root_path,
            branch_name=branch_name,
            target_path=target_path,
            start_point=start_point,
        )

        profile = WorkspaceProfile(
            file_scope=WorkspaceFileScope(
                backend=FileScopeBackend.GIT_WORKTREE,
                branch_binding=BranchBinding.SHARED,
                branch_name=branch_name,
                source_root_path=str(repository_root),
                forked_from_workspace_id=source_workspace_id,
            )
        )
        try:
            return self._repository.create(
                workspace_id=normalized_workspace_id,
                root_path=target_path,
                profile=profile,
            )
        except Exception:
            self._git_worktree_client.remove_worktree(
                repository_root=source_record.root_path,
                target_path=target_path,
            )
            self._git_worktree_client.prune(source_record.root_path)
            shutil.rmtree(
                self._workspace_storage_dir(normalized_workspace_id),
                ignore_errors=True,
            )
            raise

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
        normalized_base = self._normalize_workspace_id(base_name)
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

    def _normalize_workspace_id(self, value: str) -> str:
        base_id = _NON_WORKSPACE_ID_CHARS.sub("-", value.strip().lower()).strip("-")
        normalized = base_id or "project"
        return normalized

    def _workspace_storage_dir(self, workspace_id: str) -> Path:
        config_dir = get_project_config_dir()
        return config_dir / "workspaces" / workspace_id

    def _resolve_worktree_repository_root(self, record: WorkspaceRecord) -> Path:
        source_root_path = record.profile.file_scope.source_root_path
        if not source_root_path:
            raise ValueError(
                f"Workspace {record.workspace_id} is missing worktree source_root_path"
            )
        return Path(source_root_path).expanduser().resolve()
