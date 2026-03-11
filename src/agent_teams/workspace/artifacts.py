# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_teams.workspace.models import WorkspaceLocations, WorkspaceRef

STAGE_ROLE_TO_FILE = {
    "spec_spec": "spec.md",
    "spec_design": "design.md",
    "spec_verify": "verify.md",
}

PREVIOUS_STAGE_FILE = {
    "spec_design": "spec.md",
    "spec_coder": "design.md",
    "spec_verify": "design.md",
}


class WorkspaceArtifacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: WorkspaceRef
    locations: WorkspaceLocations

    def stage_docs_dir(self, run_id: str) -> Path:
        return self.locations.workspace_dir / "stage_docs" / run_id

    def current_stage_doc_path(self, *, run_id: str, role_id: str) -> Path:
        file_name = STAGE_ROLE_TO_FILE.get(role_id)
        if file_name is None:
            raise ValueError(f"Role does not have stage doc: {role_id}")
        return self.stage_docs_dir(run_id) / file_name

    def previous_stage_doc_path(self, *, run_id: str, role_id: str) -> Path:
        file_name = PREVIOUS_STAGE_FILE.get(role_id)
        if file_name is None:
            raise ValueError(f"Role does not have previous stage doc: {role_id}")
        return self.stage_docs_dir(run_id) / file_name

    def daily_memory_dir(self, kind: str) -> Path:
        return self.locations.workspace_dir / "memory" / "daily" / kind

    def daily_memory_path(self, *, memory_date: str, kind: str) -> Path:
        return self.daily_memory_dir(kind) / f"{memory_date}.md"

    def write_stage_doc_once(self, *, path: Path, content: str) -> None:
        if path.exists():
            raise ValueError(f"stage document already written: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
