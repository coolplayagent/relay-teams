from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.sessions.runs.enums import (
    ExecutionMode,
    InjectionSource,
    RunEventType,
)


class RunThinkingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    effort: Literal["minimal", "low", "medium", "high"] | None = None


class IntentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = False
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)


class RunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    root_task_id: str
    status: Literal["completed", "failed"]
    output: str


class InjectionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    recipient_instance_id: str = Field(min_length=1)
    source: InjectionSource
    content: str = Field(min_length=1)
    sender_instance_id: str | None = None
    sender_role_id: str | None = None
    priority: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    event_type: RunEventType
    payload_json: str = Field(default="{}")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
