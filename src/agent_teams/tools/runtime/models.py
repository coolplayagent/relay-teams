# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.shared_types.json_types import JsonObject


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    suggested_fix: str | None = None


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    tool: str = Field(min_length=1)
    data: JsonValue | None = None
    error: ToolError | None = None
    meta: JsonObject = Field(default_factory=dict)
