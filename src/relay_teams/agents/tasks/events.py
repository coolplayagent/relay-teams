from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_ASSIGNED = "task_assigned"
    TASK_STARTED = "task_started"
    TASK_STOPPED = "task_stopped"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_TIMEOUT = "task_timeout"
    INSTANCE_CREATED = "instance_created"
    INSTANCE_STOPPED = "instance_stopped"
    INSTANCE_RECYCLED = "instance_recycled"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = None
    instance_id: str | None = None
    payload_json: str = Field(default="{}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
