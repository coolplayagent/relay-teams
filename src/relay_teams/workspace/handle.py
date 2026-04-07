# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from relay_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceProfile,
    WorkspaceRef,
)


class WorkspaceHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    _TMP_PREFIXES: ClassVar[tuple[str, str]] = ("tmp/", "tmp\\")

    ref: WorkspaceRef
    profile: WorkspaceProfile
    locations: WorkspaceLocations

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

    def _validate_allowed_path(self, path: Path, *, write: bool, raw_path: str) -> Path:
        allowed_roots = (
            self.locations.writable_roots if write else self.locations.readable_roots
        )
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

    def _resolve_candidate_path(self, raw_path: str) -> Path:
        normalized_path = self._normalize_raw_path(raw_path)
        p = Path(normalized_path)
        if p.is_absolute():
            return p.resolve()
        if self._uses_workspace_tmp_root(normalized_path):
            relative_to_tmp = normalized_path.removeprefix("tmp").lstrip("/\\")
            return (self.tmp_root / relative_to_tmp).resolve()
        return (self.execution_root / normalized_path).resolve()

    def resolve_read_path(self, path: str) -> Path:
        return self._resolve_candidate_path(path)

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        candidate = self._resolve_candidate_path(relative_path)
        if write:
            return self._validate_allowed_path(
                candidate,
                write=True,
                raw_path=relative_path,
            )
        return candidate

    def resolve_tmp_path(self, relative_path: str, *, write: bool = True) -> Path:
        requested_path = Path(relative_path)
        if requested_path.is_absolute():
            raise ValueError(
                f"Path must be relative to the workspace tmp directory: {relative_path}"
            )

        candidate = (self.tmp_root / requested_path).resolve()
        if candidate == self.tmp_root:
            raise ValueError(
                "Path must point to a file inside the workspace tmp directory"
            )
        if not self._is_path_within_root(candidate, self.tmp_root):
            raise ValueError(
                f"Path is outside workspace tmp directory: {relative_path}"
            )
        return self._validate_allowed_path(
            candidate,
            write=write,
            raw_path=f"tmp/{relative_path}",
        )

    def logical_tmp_path(self, path: Path) -> str:
        resolved_path = path.resolve()
        if resolved_path == self.tmp_root.resolve():
            return "tmp"
        relative_path = resolved_path.relative_to(self.tmp_root.resolve())
        return Path("tmp", relative_path).as_posix()

    def resolve_workdir(self, relative_path: str | None = None) -> Path:
        raw_path = "." if relative_path is None else relative_path
        candidate = (
            self.execution_root
            if relative_path is None
            else self._resolve_candidate_path(relative_path)
        )
        return self._validate_allowed_path(
            candidate,
            write=True,
            raw_path=raw_path,
        )
