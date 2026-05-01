# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class AuditEventType(str, Enum):
    FILE_WRITE = "file_write"
    SHELL_COMMAND = "shell_command"
    COORDINATOR_DECISION = "coordinator_decision"


def new_audit_event_id() -> str:
    return f"audit_{uuid4().hex[:16]}"


class AuditEventCreate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    audit_event_id: str = Field(default_factory=new_audit_event_id)
    event_type: AuditEventType
    trace_id: str
    run_id: str
    session_id: str
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    tool_call_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    action: str
    target: str
    content_digest: str | None = None
    content_size_bytes: int | None = None
    command: str | None = None
    decision_reason: str | None = None
    outcome: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _validate_event_specific_fields(self) -> AuditEventCreate:
        if self.event_type == AuditEventType.FILE_WRITE and not self.target.strip():
            raise ValueError("file write audit events require target")
        if self.event_type == AuditEventType.SHELL_COMMAND and not self.command:
            raise ValueError("shell command audit events require command")
        if (
            self.event_type == AuditEventType.COORDINATOR_DECISION
            and not self.decision_reason
        ):
            raise ValueError("coordinator decision audit events require reason")
        return self


class AuditEventRecord(AuditEventCreate):
    id: int
    created_at: datetime


class AuditEventFilter(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    event_type: AuditEventType | None = None
    trace_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    role_id: str | None = None
    after_id: int = Field(default=0, ge=0)
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=100, ge=1, le=500)


class AuditEventPage(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AuditEventRecord, ...]
    next_after_id: int | None = None
