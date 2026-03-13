# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_teams.workspace.ids import build_workspace_id


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    has_active_run: bool = False
    active_run_id: str | None = None
    active_run_status: str | None = None
    active_run_phase: str | None = None
    pending_tool_approval_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @model_validator(mode="before")
    @classmethod
    def _populate_workspace_id(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        payload = dict(data)
        session_id = payload.get("session_id")
        workspace_id = payload.get("workspace_id")
        if isinstance(session_id, str) and session_id and not workspace_id:
            payload["workspace_id"] = build_workspace_id(session_id)
        return payload
