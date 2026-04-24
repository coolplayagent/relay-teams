from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ReminderKind(str, Enum):
    TOOL_FAILURE = "tool_failure"
    READ_ONLY_STREAK = "read_only_streak"
    INCOMPLETE_TODOS = "incomplete_todos"
    CONTEXT_PRESSURE = "context_pressure"
    POST_COMPACTION = "post_compaction"


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    NEUTRAL = "neutral"


class IncompleteTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str = Field(min_length=1)
    status: str = Field(min_length=1)


class ToolResultObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    task_id: str | None = None
    instance_id: str
    role_id: str
    tool_name: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    ok: bool
    error_type: str = ""
    error_message: str = ""
    retryable: bool = False
    meta: dict[str, JsonValue] = Field(default_factory=dict)


class CompletionAttemptObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    task_id: str
    instance_id: str
    role_id: str
    workspace_id: str
    conversation_id: str
    output_text: str = ""
    incomplete_todos: tuple[IncompleteTodoItem, ...] = ()


class ContextPressureObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    task_id: str | None = None
    instance_id: str
    role_id: str
    conversation_id: str
    kind: ReminderKind
    message_count_before: int = Field(default=0, ge=0)
    message_count_after: int = Field(default=0, ge=0)
    estimated_tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    threshold_tokens: int = Field(default=0, ge=0)
    target_tokens: int = Field(default=0, ge=0)


class ReminderDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    issue: bool = False
    kind: ReminderKind | None = None
    issue_key: str = ""
    content: str = ""
    retry_completion: bool = False
    fail_completion: bool = False
    reason: str = ""
