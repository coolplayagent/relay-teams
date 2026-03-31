from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: tuple[str, ...] = Field(min_length=1)


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    parent_task_id: OptionalIdentifierStr = None
    trace_id: RequiredIdentifierStr
    role_id: OptionalIdentifierStr = "Coordinator"
    title: str | None = None
    objective: str = Field(min_length=1)
    verification: VerificationPlan


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: TaskEnvelope
    status: TaskStatus = TaskStatus.CREATED
    assigned_instance_id: OptionalIdentifierStr = None
    result: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: RequiredIdentifierStr
    passed: bool
    details: tuple[str, ...]
