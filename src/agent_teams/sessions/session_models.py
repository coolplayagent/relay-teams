# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SessionMode(str, Enum):
    NORMAL = "normal"
    ORCHESTRATION = "orchestration"


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    session_mode: SessionMode = SessionMode.NORMAL
    orchestration_preset_id: str | None = None
    started_at: datetime | None = None
    can_switch_mode: bool = True
    has_active_run: bool = False
    active_run_id: str | None = None
    active_run_status: str | None = None
    active_run_phase: str | None = None
    pending_tool_approval_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
