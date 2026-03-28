# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.workspace.workspace_models import (
    WorkspaceLocations,
    WorkspaceProfile,
    WorkspaceRef,
)


class WorkspaceHandle(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    ref: WorkspaceRef
    profile: WorkspaceProfile
    locations: WorkspaceLocations

    @property
    def root_path(self) -> Path:
        return self.locations.execution_root

    def _resolve_candidate_path(self, raw_path: str) -> Path:
        import sys
        import re

        # Convert MSYS2/Git Bash absolute paths to Windows paths
        normalized_path = raw_path
        if sys.platform == "win32" and normalized_path.startswith("/"):
            m = re.match(r"^/([a-zA-Z])/(.*)", normalized_path)
            if m:
                normalized_path = f"{m.group(1)}:/{m.group(2)}"

        p = Path(normalized_path)
        if p.is_absolute():
            return p.resolve()
        return (self.root_path / normalized_path).resolve()

    def resolve_read_path(self, path: str) -> Path:
        return self._resolve_candidate_path(path)

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        candidate = self._resolve_candidate_path(relative_path)

        allowed_roots = (
            self.locations.writable_roots if write else self.locations.readable_roots
        )
        for allowed_root in allowed_roots:
            resolved_root = allowed_root.resolve()
            if candidate == resolved_root or resolved_root in candidate.parents:
                return candidate
        action = "write" if write else "read"
        raise ValueError(f"Path is outside workspace {action} scope: {relative_path}")

    def resolve_workdir(self, relative_path: str | None = None) -> Path:
        if relative_path is None:
            return self.root_path
        return self.resolve_path(relative_path, write=False)
