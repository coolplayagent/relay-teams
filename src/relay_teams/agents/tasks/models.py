from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_identifier_tuple,
)


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
    skills: Optional[tuple[str, ...]] = None
    verification: VerificationPlan

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> Optional[tuple[str, ...]]:
        return normalize_identifier_tuple(value, field_name="skills")


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
