# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path, PurePosixPath
import posixpath
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceSshMountConfig,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceRef,
    WorkspaceProfile,
    default_workspace_profile,
    legacy_workspace_mount_from_profile,
)


class ResolvedWorkspacePath(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    mount_name: str | None
    provider: WorkspaceMountProvider
    logical_path: str
    local_path: Path | None = None
    remote_path: str | None = None
    host_bypass: bool = False


class WorkspaceHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    _TMP_PREFIXES: ClassVar[tuple[str, str]] = ("tmp/", "tmp\\")

    ref: WorkspaceRef
    mounts: tuple[WorkspaceMountRecord, ...] = ()
    locations: WorkspaceLocations

    @classmethod
    def _resolve_legacy_profile(cls, data: dict[str, object]) -> WorkspaceProfile:
        raw_profile = data.get("profile")
        if isinstance(raw_profile, WorkspaceProfile):
            return raw_profile
        if isinstance(raw_profile, dict):
            return WorkspaceProfile.model_validate(raw_profile)
        return default_workspace_profile()

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_mounts(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "mounts" in data:
            coerced_with_mounts = dict(data)
            coerced_with_mounts.pop("profile", None)
            return coerced_with_mounts
        raw_locations = data.get("locations")
        raw_ref = data.get("ref")
        if not isinstance(raw_locations, WorkspaceLocations) or not isinstance(
            raw_ref, WorkspaceRef
        ):
            return data
        profile = cls._resolve_legacy_profile(data)
        mount = legacy_workspace_mount_from_profile(
            root_path=raw_locations.scope_root,
            profile=profile,
            mount_name=raw_ref.default_mount_name,
        )
        coerced = dict(data)
        coerced.pop("profile", None)
        coerced["mounts"] = (mount,)
        return coerced

    @property
    def scope_root(self) -> Path:
        return self.locations.scope_root

    @property
    def execution_root(self) -> Path:
        return self.locations.execution_root

    @property
    def tmp_root(self) -> Path:
        return self.locations.tmp_root

    @property
    def root_path(self) -> Path:
        return self.execution_root

    @property
    def default_mount_name(self) -> str:
        return self.ref.default_mount_name

    @property
    def default_mount(self) -> WorkspaceMountRecord:
        return self.mount_by_name(self.default_mount_name)

    def mount_by_name(self, mount_name: str) -> WorkspaceMountRecord:
        for mount in self.mounts:
            if mount.mount_name == mount_name:
                return mount
        raise KeyError(f"Unknown workspace mount: {mount_name}")

    def first_local_mount(self) -> WorkspaceMountRecord | None:
        for mount in self.mounts:
            if mount.provider == WorkspaceMountProvider.LOCAL:
                return mount
        return None

    def _normalize_raw_path(self, raw_path: str) -> str:
        import re
        import sys

        normalized_path = raw_path
        if sys.platform == "win32" and normalized_path.startswith("/"):
            match = re.match(r"^/([a-zA-Z])/(.*)", normalized_path)
            if match is not None:
                normalized_path = f"{match.group(1)}:/{match.group(2)}"
        return normalized_path

    def _uses_workspace_tmp_root(self, raw_path: str) -> bool:
        return raw_path == "tmp" or raw_path.startswith(self._TMP_PREFIXES)

    @staticmethod
    def _is_path_within_root(candidate: Path, root: Path) -> bool:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
        return (
            resolved_candidate == resolved_root
            or resolved_root in resolved_candidate.parents
        )

    @staticmethod
    def _is_posix_path_within_root(candidate: str, root: str) -> bool:
        normalized_root = posixpath.normpath(root)
        normalized_candidate = posixpath.normpath(candidate)
        if normalized_root == "/":
            return normalized_candidate.startswith("/")
        return (
            normalized_candidate == normalized_root
            or normalized_candidate.startswith(normalized_root.rstrip("/") + "/")
        )

    def _validate_allowed_local_path(
        self,
        path: Path,
        *,
        allowed_roots: tuple[Path, ...],
        raw_path: str,
        write: bool,
    ) -> Path:
        resolved_candidate = path.resolve()
        for allowed_root in allowed_roots:
            if self._is_path_within_root(resolved_candidate, allowed_root):
                return resolved_candidate
        action = "write" if write else "read"
        allowed_roots_text = ", ".join(str(root.resolve()) for root in allowed_roots)
        raise ValueError(
            f"Path is outside workspace {action} scope: requested={raw_path}, "
            f"resolved={resolved_candidate}, allowed_roots=[{allowed_roots_text}]"
        )

    def _parse_mount_prefix(self, raw_path: str) -> tuple[str | None, str]:
        if ":/" not in raw_path:
            return None, raw_path
        mount_name, logical_path = raw_path.split(":/", maxsplit=1)
        if not mount_name.strip():
            raise ValueError(f"Invalid workspace mount path: {raw_path}")
        return mount_name.strip(), logical_path or "."

    def _resolve_local_mount_roots(
        self,
        mount: WorkspaceMountRecord,
        *,
        write: bool,
    ) -> tuple[Path, ...]:
        root_path = mount.local_root_path()
        if root_path is None and mount.provider == WorkspaceMountProvider.SSH:
            root_path = self._ssh_local_mount_root(mount.mount_name)
        if root_path is None:
            raise ValueError(f"Workspace mount is not local: {mount.mount_name}")
        if (
            mount.mount_name == self.locations.mount_name
            and mount.provider == self.locations.provider
            and root_path.resolve() == self.locations.scope_root.resolve()
        ):
            location_roots = (
                self.locations.writable_roots
                if write
                else self.locations.readable_roots
            )
            deduped_roots: list[Path] = []
            seen: set[Path] = set()
            for candidate in (self.execution_root, *location_roots):
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                deduped_roots.append(resolved)
                seen.add(resolved)
            return tuple(deduped_roots)
        raw_paths = mount.writable_paths if write else mount.readable_paths
        return tuple(
            self._resolve_local_relative_root(root_path, raw_path)
            for raw_path in raw_paths
        )

    def _resolve_local_relative_root(self, root_path: Path, relative_path: str) -> Path:
        candidate = (root_path / relative_path).resolve()
        resolved_root = root_path.resolve()
        if candidate != resolved_root and resolved_root not in candidate.parents:
            raise ValueError(
                f"Workspace file scope escapes mount root: {relative_path}"
            )
        return candidate

    def _resolve_remote_path(self, mount: WorkspaceMountRecord, raw_path: str) -> str:
        if mount.provider != WorkspaceMountProvider.SSH:
            raise ValueError(f"Workspace mount is not ssh: {mount.mount_name}")
        provider_config = mount.provider_config
        if not isinstance(provider_config, WorkspaceSshMountConfig):
            raise ValueError(
                f"Workspace ssh mount is missing ssh config: {mount.mount_name}"
            )
        remote_root = str(provider_config.remote_root).strip()
        normalized_root = posixpath.normpath(remote_root or "/")
        logical_path = posixpath.normpath(
            posixpath.join(normalized_root, raw_path.lstrip("/"))
        )
        if logical_path != normalized_root and not logical_path.startswith(
            normalized_root.rstrip("/") + "/"
        ):
            raise ValueError(f"Workspace path escapes ssh mount root: {raw_path}")
        return logical_path

    def _ssh_local_mount_root(self, mount_name: str) -> Path | None:
        for remote_mount_root in self.locations.remote_mount_roots:
            if remote_mount_root.mount_name == mount_name:
                return remote_mount_root.local_root.resolve()
        return None

    def _resolve_absolute_local_workspace_path(
        self,
        candidate: Path,
        *,
        raw_path: str,
        write: bool,
    ) -> ResolvedWorkspacePath | None:
        resolved_candidate = candidate.resolve()
        for mount in self.mounts:
            if mount.provider != WorkspaceMountProvider.LOCAL:
                continue
            root_path = mount.local_root_path()
            if root_path is None:
                continue
            try:
                allowed_roots = self._resolve_local_mount_roots(mount, write=write)
                resolved_path = self._validate_allowed_local_path(
                    resolved_candidate,
                    allowed_roots=allowed_roots,
                    raw_path=raw_path,
                    write=write,
                )
            except ValueError:
                continue
            if self._is_path_within_root(resolved_path, root_path):
                logical_path = (
                    resolved_path.relative_to(root_path.resolve()).as_posix()
                    if resolved_path != root_path.resolve()
                    else "."
                )
            else:
                logical_path = resolved_path.as_posix()
            return ResolvedWorkspacePath(
                mount_name=mount.mount_name,
                provider=WorkspaceMountProvider.LOCAL,
                logical_path=logical_path,
                local_path=resolved_path,
            )
        return None

    def _resolve_absolute_ssh_workspace_path(
        self,
        candidate: Path,
        *,
        normalized_path: str,
        raw_path: str,
        write: bool,
    ) -> ResolvedWorkspacePath | None:
        resolved_candidate = candidate.resolve()
        remote_candidate = posixpath.normpath(normalized_path.replace("\\", "/"))
        for remote_mount_root in self.locations.remote_mount_roots:
            mount = self.mount_by_name(remote_mount_root.mount_name)
            if mount.provider != WorkspaceMountProvider.SSH:
                continue
            local_root = remote_mount_root.local_root.resolve()
            remote_root = posixpath.normpath(remote_mount_root.remote_root.strip())
            logical_path: str | None = None
            if self._is_path_within_root(resolved_candidate, local_root):
                logical_path = (
                    resolved_candidate.relative_to(local_root).as_posix()
                    if resolved_candidate != local_root
                    else "."
                )
            elif self._is_posix_path_within_root(remote_candidate, remote_root):
                logical_path = (
                    posixpath.relpath(remote_candidate, remote_root)
                    if remote_candidate != remote_root
                    else "."
                )
            if logical_path is None:
                continue
            local_path = self._validate_allowed_local_path(
                (local_root / logical_path).resolve(),
                allowed_roots=self._resolve_local_mount_roots(mount, write=write),
                raw_path=raw_path,
                write=write,
            )
            return ResolvedWorkspacePath(
                mount_name=mount.mount_name,
                provider=WorkspaceMountProvider.SSH,
                logical_path=logical_path,
                local_path=local_path,
                remote_path=self._resolve_remote_path(mount, logical_path),
            )
        return None

    def resolve_workspace_path(
        self,
        raw_path: str,
        *,
        write: bool = False,
        allow_host_read_bypass: bool = False,
    ) -> ResolvedWorkspacePath:
        normalized_path = self._normalize_raw_path(raw_path)
        path_obj = Path(normalized_path)
        if path_obj.is_absolute():
            resolved_absolute = self._resolve_absolute_local_workspace_path(
                path_obj,
                raw_path=raw_path,
                write=write,
            )
            if resolved_absolute is not None:
                return resolved_absolute
            resolved_ssh_absolute = self._resolve_absolute_ssh_workspace_path(
                path_obj,
                normalized_path=normalized_path,
                raw_path=raw_path,
                write=write,
            )
            if resolved_ssh_absolute is not None:
                return resolved_ssh_absolute
            if allow_host_read_bypass and not write:
                return ResolvedWorkspacePath(
                    mount_name=None,
                    provider=WorkspaceMountProvider.LOCAL,
                    logical_path=str(path_obj),
                    local_path=path_obj.resolve(),
                    host_bypass=True,
                )
            action = "write" if write else "read"
            raise ValueError(
                f"Path is outside workspace {action} scope: requested={raw_path}, "
                f"resolved={path_obj.resolve()}"
            )
        if self._uses_workspace_tmp_root(normalized_path):
            relative_to_tmp = normalized_path.removeprefix("tmp").lstrip("/\\")
            candidate = (self.tmp_root / relative_to_tmp).resolve()
            allowed_roots = (
                (self.tmp_root.resolve(),) if write else (self.tmp_root.resolve(),)
            )
            resolved = self._validate_allowed_local_path(
                candidate,
                allowed_roots=allowed_roots,
                raw_path=raw_path,
                write=write,
            )
            return ResolvedWorkspacePath(
                mount_name="tmp",
                provider=WorkspaceMountProvider.LOCAL,
                logical_path=PurePosixPath("tmp", relative_to_tmp or ".").as_posix(),
                local_path=resolved,
            )
        mount_name, logical_path = self._parse_mount_prefix(normalized_path)
        resolved_mount = (
            self.mount_by_name(mount_name)
            if mount_name is not None
            else self.default_mount
        )
        normalized_logical_path = (
            PurePosixPath(logical_path).as_posix() if logical_path.strip() else "."
        )
        if resolved_mount.provider == WorkspaceMountProvider.LOCAL:
            root_path = resolved_mount.local_root_path()
            if root_path is None:
                raise ValueError(
                    f"Workspace mount is not local: {resolved_mount.mount_name}"
                )
            candidate_root = root_path
            if (
                mount_name is None
                and resolved_mount.mount_name == self.locations.mount_name
                and resolved_mount.provider == self.locations.provider
                and self._is_path_within_root(self.execution_root, root_path)
            ):
                candidate_root = self.execution_root
            candidate = (candidate_root / normalized_logical_path).resolve()
            allowed_roots = self._resolve_local_mount_roots(
                resolved_mount,
                write=write,
            )
            resolved_path = self._validate_allowed_local_path(
                candidate,
                allowed_roots=allowed_roots,
                raw_path=raw_path,
                write=write,
            )
            return ResolvedWorkspacePath(
                mount_name=resolved_mount.mount_name,
                provider=resolved_mount.provider,
                logical_path=normalized_logical_path,
                local_path=resolved_path,
            )
        remote_path = self._resolve_remote_path(resolved_mount, normalized_logical_path)
        local_root = self._ssh_local_mount_root(resolved_mount.mount_name)
        local_path = None
        if local_root is not None:
            local_path = self._validate_allowed_local_path(
                (local_root / normalized_logical_path).resolve(),
                allowed_roots=self._resolve_local_mount_roots(
                    resolved_mount,
                    write=write,
                ),
                raw_path=raw_path,
                write=write,
            )
        return ResolvedWorkspacePath(
            mount_name=resolved_mount.mount_name,
            provider=resolved_mount.provider,
            logical_path=normalized_logical_path,
            local_path=local_path,
            remote_path=remote_path,
        )

    def resolve_read_path(self, path: str) -> Path:
        resolved = self.resolve_workspace_path(
            path,
            write=False,
            allow_host_read_bypass=True,
        )
        if resolved.local_path is None:
            raise ValueError(
                f"Workspace path resolves to non-local mount: {resolved.mount_name}"
            )
        return resolved.local_path

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        resolved = self.resolve_workspace_path(relative_path, write=write)
        if resolved.local_path is None:
            raise ValueError(
                f"Workspace path resolves to non-local mount: {resolved.mount_name}"
            )
        return resolved.local_path

    def resolve_tmp_path(self, relative_path: str, *, write: bool = True) -> Path:
        requested_path = Path(relative_path)
        if requested_path.is_absolute():
            raise ValueError(
                f"Path must be relative to the workspace tmp directory: {relative_path}"
            )
        try:
            resolved = self.resolve_workspace_path(
                PurePosixPath("tmp", relative_path).as_posix(),
                write=write,
            )
        except ValueError as exc:
            raise ValueError(
                f"Path is outside workspace tmp directory: {relative_path}"
            ) from exc
        if resolved.local_path is None:
            raise ValueError("Workspace tmp path must be local")
        return resolved.local_path

    def logical_tmp_path(self, path: Path) -> str:
        resolved_path = path.resolve()
        if resolved_path == self.tmp_root.resolve():
            return "tmp"
        relative_path = resolved_path.relative_to(self.tmp_root.resolve())
        return Path("tmp", relative_path).as_posix()

    def resolve_workdir(self, relative_path: str | None = None) -> Path:
        raw_path = "." if relative_path is None else relative_path
        resolved = self.resolve_workspace_path(raw_path, write=True)
        if resolved.local_path is None:
            raise ValueError(
                f"Workspace workdir resolves to non-local mount: {resolved.mount_name}"
            )
        return resolved.local_path
