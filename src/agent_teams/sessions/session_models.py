# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class SessionMode(str, Enum):
    NORMAL = "normal"
    ORCHESTRATION = "orchestration"


class ProjectKind(str, Enum):
    WORKSPACE = "workspace"
    AUTOMATION = "automation"


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    workspace_id: RequiredIdentifierStr
    project_kind: ProjectKind = ProjectKind.WORKSPACE
    project_id: OptionalIdentifierStr = None
    metadata: dict[str, str] = Field(default_factory=dict)
    session_mode: SessionMode = SessionMode.NORMAL
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None
    started_at: datetime | None = None
    can_switch_mode: bool = True
    has_active_run: bool = False
    active_run_id: OptionalIdentifierStr = None
    active_run_status: str | None = None
    active_run_phase: str | None = None
    pending_tool_approval_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="after")
    def _default_project_id(self) -> SessionRecord:
        if self.project_id is None or not self.project_id.strip():
            self.project_id = self.workspace_id
        return self
