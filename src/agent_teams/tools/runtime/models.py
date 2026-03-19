# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: JsonValue | None = None
    error: ToolError | None = None


class ToolInternalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    visible_result: ToolResultEnvelope
    internal_data: JsonValue | None = None
    runtime_meta: dict[str, JsonValue] = Field(default_factory=dict)


class ToolResultProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    visible_data: JsonValue | None = None
    internal_data: JsonValue | None = None
