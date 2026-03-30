# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.computer import (
    ComputerActionRisk,
    ComputerPermissionScope,
    ExecutionSurface,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_serializer


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        payload = handler(self)
        if not self.details:
            payload.pop("details", None)
        return payload


class ToolExecutionError(RuntimeError):
    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        retryable: bool = False,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.details = {} if details is None else dict(details)


class ToolResultEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    data: JsonValue | None = None
    error: ToolError | None = None
    meta: dict[str, JsonValue] = Field(default_factory=dict)


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


class ToolApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    permission_scope: ComputerPermissionScope | None = None
    risk_level: ComputerActionRisk | None = None
    target_summary: str = ""
    source: str = ""
    execution_surface: ExecutionSurface | None = None


class ToolApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required: bool
    permission_scope: ComputerPermissionScope | None = None
    risk_level: ComputerActionRisk | None = None
    target_summary: str = ""
    source: str = ""
    execution_surface: ExecutionSurface | None = None
