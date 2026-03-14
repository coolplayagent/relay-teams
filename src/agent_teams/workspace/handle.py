# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.workspace.models import (
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

    def resolve_path(self, relative_path: str, *, write: bool = False) -> Path:
        import sys
        import re

        # Convert MSYS2/Git Bash absolute paths to Windows paths
        if sys.platform == "win32" and relative_path.startswith("/"):
            m = re.match(r"^/([a-zA-Z])/(.*)", relative_path)
            if m:
                relative_path = f"{m.group(1)}:/{m.group(2)}"

        p = Path(relative_path)
        if p.is_absolute():
            candidate = p.resolve()
        else:
            candidate = (self.root_path / relative_path).resolve()

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
