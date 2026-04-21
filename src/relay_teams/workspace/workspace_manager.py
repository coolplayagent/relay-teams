# -*- coding: utf-8 -*-
from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from relay_teams.paths import get_project_config_dir
from relay_teams.workspace.handle import WorkspaceHandle
from relay_teams.workspace.ids import build_conversation_id
from relay_teams.workspace.ssh_profile_service import SshProfileService
from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceMountProvider,
    WorkspaceRemoteMountRoot,
    WorkspaceRecord,
    WorkspaceRef,
    WorkspaceSshMountConfig,
    default_workspace_profile,
    legacy_workspace_mount_from_profile,
)
from relay_teams.workspace.workspace_repository import WorkspaceRepository


class WorkspaceManager(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    project_root: Path
    app_config_dir: Path | None = None
    workspace_repo: WorkspaceRepository | None = None
    shared_store: object | None = None
    builtin_skills_dir: Path | None = None
    app_skills_dir: Path | None = None
    ssh_profile_service: SshProfileService | None = None

    def resolve(
        self,
        *,
        session_id: str,
        role_id: str,
        instance_id: str | None,
        profile: object | None = None,
        workspace_id: str,
        conversation_id: str | None = None,
    ) -> WorkspaceHandle:
        del profile
        resolved_conversation_id = conversation_id or build_conversation_id(
            session_id, role_id
        )
        record = self._resolve_record(workspace_id)
        ref = WorkspaceRef(
            workspace_id=record.workspace_id,
            session_id=session_id,
            role_id=role_id,
            conversation_id=resolved_conversation_id,
            default_mount_name=record.default_mount_name,
            mount_names=tuple(mount.mount_name for mount in record.mounts),
            instance_id=instance_id,
        )
        locations = self._resolve_locations(record=record)
        return WorkspaceHandle(
            ref=ref,
            mounts=record.mounts,
            locations=locations,
        )

    def locations_for(self, workspace_id: str) -> WorkspaceLocations:
        record = self._resolve_record(workspace_id)
        config_dir = self._resolve_app_config_dir(
            project_root=self._config_root(record)
        )
        workspace_dir = config_dir / "workspaces" / workspace_id
        tmp_root = workspace_dir / "tmp"
        remote_mount_roots = self._materialize_remote_mounts(
            record=record,
            workspace_dir=workspace_dir,
        )
        default_remote_root = self._remote_mount_root_by_name(
            remote_mount_roots,
            record.default_mount_name,
        )
        if default_remote_root is not None:
            return self._build_ssh_mount_locations(
                workspace_dir=workspace_dir,
                mount=record.default_mount,
                remote_mount_root=default_remote_root,
                tmp_root=tmp_root,
                remote_mount_roots=remote_mount_roots,
            )
        primary_mount = self._primary_local_mount(record)
        if primary_mount is None:
            fallback_scope_root = tmp_root
            return WorkspaceLocations(
                workspace_dir=workspace_dir,
                mount_name=record.default_mount_name,
                provider=record.default_mount.provider,
                scope_root=fallback_scope_root,
                execution_root=fallback_scope_root,
                tmp_root=tmp_root,
                readable_roots=(fallback_scope_root,),
                writable_roots=(fallback_scope_root,),
                remote_mount_roots=remote_mount_roots,
            )
        return self._build_local_mount_locations(
            workspace_dir=workspace_dir,
            mount=primary_mount,
            tmp_root=tmp_root,
            remote_mount_roots=remote_mount_roots,
        )

    def delete_workspace(self, workspace_id: str) -> None:
        shutil.rmtree(
            self.locations_for(workspace_id).workspace_dir, ignore_errors=True
        )

    def session_artifact_dir(self, *, workspace_id: str, session_id: str) -> Path:
        record = self._resolve_record(workspace_id)
        config_dir = self._resolve_app_config_dir(
            project_root=self._config_root(record)
        )
        return config_dir / "sessions" / workspace_id / session_id

    def _resolve_locations(self, *, record: WorkspaceRecord) -> WorkspaceLocations:
        return self.locations_for(record.workspace_id)

    def _build_local_mount_locations(
        self,
        *,
        workspace_dir: Path,
        mount,
        tmp_root: Path,
        remote_mount_roots: tuple[WorkspaceRemoteMountRoot, ...] = (),
    ) -> WorkspaceLocations:
        root_path = mount.local_root_path()
        if root_path is None:
            raise ValueError(f"Workspace mount is not local: {mount.mount_name}")
        execution_root = self._resolve_relative_root(root_path, mount.working_directory)
        readable_roots = self._append_unique_roots(
            tuple(
                self._resolve_relative_root(root_path, raw_path)
                for raw_path in mount.readable_paths
            ),
            (tmp_root, *self._skill_roots()),
        )
        writable_roots = self._append_unique_roots(
            tuple(
                self._resolve_relative_root(root_path, raw_path)
                for raw_path in mount.writable_paths
            ),
            (tmp_root,),
        )
        return WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name=mount.mount_name,
            provider=WorkspaceMountProvider.LOCAL,
            scope_root=root_path,
            execution_root=execution_root,
            tmp_root=tmp_root,
            readable_roots=readable_roots,
            writable_roots=writable_roots,
            remote_mount_roots=remote_mount_roots,
            worktree_root=root_path if mount.source_root_path is not None else None,
            branch_name=mount.branch_name,
        )

    def _build_ssh_mount_locations(
        self,
        *,
        workspace_dir: Path,
        mount,
        remote_mount_root: WorkspaceRemoteMountRoot,
        tmp_root: Path,
        remote_mount_roots: tuple[WorkspaceRemoteMountRoot, ...],
    ) -> WorkspaceLocations:
        execution_root = self._resolve_relative_root(
            remote_mount_root.local_root,
            mount.working_directory,
        )
        readable_roots = self._append_unique_roots(
            tuple(
                self._resolve_relative_root(remote_mount_root.local_root, raw_path)
                for raw_path in mount.readable_paths
            ),
            (tmp_root, *self._skill_roots()),
        )
        writable_roots = self._append_unique_roots(
            tuple(
                self._resolve_relative_root(remote_mount_root.local_root, raw_path)
                for raw_path in mount.writable_paths
            ),
            (tmp_root,),
        )
        return WorkspaceLocations(
            workspace_dir=workspace_dir,
            mount_name=mount.mount_name,
            provider=WorkspaceMountProvider.SSH,
            scope_root=remote_mount_root.local_root,
            execution_root=execution_root,
            tmp_root=tmp_root,
            readable_roots=readable_roots,
            writable_roots=writable_roots,
            remote_mount_roots=remote_mount_roots,
        )

    def _materialize_remote_mounts(
        self,
        *,
        record: WorkspaceRecord,
        workspace_dir: Path,
    ) -> tuple[WorkspaceRemoteMountRoot, ...]:
        remote_roots: list[WorkspaceRemoteMountRoot] = []
        for mount in record.mounts:
            if mount.provider != WorkspaceMountProvider.SSH:
                continue
            provider_config = mount.provider_config
            if not isinstance(provider_config, WorkspaceSshMountConfig):
                raise ValueError(
                    f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
                )
            local_root = (workspace_dir / "ssh_mounts" / mount.mount_name).resolve()
            if self.ssh_profile_service is None:
                raise ValueError(
                    "Workspace ssh mount requires ssh profile service: "
                    f"{mount.mount_name}"
                )
            local_root.mkdir(parents=True, exist_ok=True)
            self.ssh_profile_service.ensure_filesystem_mount(
                ssh_profile_id=provider_config.ssh_profile_id,
                remote_root=provider_config.remote_root,
                local_root=local_root,
            )
            remote_roots.append(
                WorkspaceRemoteMountRoot(
                    mount_name=mount.mount_name,
                    local_root=local_root,
                    remote_root=provider_config.remote_root,
                )
            )
        return tuple(remote_roots)

    def _remote_mount_root_by_name(
        self,
        remote_mount_roots: tuple[WorkspaceRemoteMountRoot, ...],
        mount_name: str,
    ) -> WorkspaceRemoteMountRoot | None:
        for remote_mount_root in remote_mount_roots:
            if remote_mount_root.mount_name == mount_name:
                return remote_mount_root
        return None

    def _append_unique_roots(
        self,
        roots: tuple[Path, ...],
        extra_roots: tuple[Path, ...],
    ) -> tuple[Path, ...]:
        deduped: list[Path] = []
        seen: set[Path] = set()
        for candidate in (*roots, *extra_roots):
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            deduped.append(resolved)
            seen.add(resolved)
        return tuple(deduped)

    def _skill_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for candidate in (self.builtin_skills_dir, self.app_skills_dir):
            if candidate is None:
                continue
            roots.append(candidate.expanduser().resolve())
        return tuple(roots)

    def _resolve_relative_root(self, filesystem_root: Path, relative_path: str) -> Path:
        candidate = (filesystem_root / relative_path).resolve()
        resolved_root = filesystem_root.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise ValueError(
                f"Workspace file scope escapes mount root: {relative_path}"
            )
        return candidate

    def _resolve_record(self, workspace_id: str) -> WorkspaceRecord:
        if self.workspace_repo is not None and self.workspace_repo.exists(workspace_id):
            return self.workspace_repo.get(workspace_id)
        mount = legacy_workspace_mount_from_profile(
            root_path=self.project_root.resolve(),
            profile=default_workspace_profile(),
        )
        return WorkspaceRecord(
            workspace_id=workspace_id,
            default_mount_name="default",
            mounts=(mount,),
        )

    def _primary_local_mount(self, record: WorkspaceRecord):
        default_mount = record.default_mount
        if default_mount.provider == WorkspaceMountProvider.LOCAL:
            return default_mount
        return record.first_local_mount()

    def _config_root(self, record: WorkspaceRecord) -> Path:
        local_mount = self._primary_local_mount(record)
        if local_mount is not None:
            local_root = local_mount.local_root_path()
            if local_root is not None:
                return local_root
        return self.project_root.resolve()

    def _resolve_app_config_dir(self, *, project_root: Path) -> Path:
        if self.app_config_dir is not None:
            return self.app_config_dir.expanduser().resolve()
        return get_project_config_dir(project_root=project_root)
