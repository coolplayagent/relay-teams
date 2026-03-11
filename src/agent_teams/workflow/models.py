from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.workflow.enums import TaskStatus


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: tuple[str, ...] = Field(min_length=1)


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    parent_task_id: str | None = None
    trace_id: str = Field(min_length=1)
    role_id: str = Field(default="coordinator_agent", min_length=1)
    title: str | None = None
    objective: str = Field(min_length=1)
    verification: VerificationPlan


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: TaskEnvelope
    status: TaskStatus = TaskStatus.CREATED
    assigned_instance_id: str | None = None
    result: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    passed: bool
    details: tuple[str, ...]
