from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.core.enums import EventType, ExecutionMode, InjectionSource, InstanceStatus, RunEventType, ScopeType, TaskStatus


class SamplingConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024, ge=1)
    top_k: int | None = Field(default=None, ge=1)


class ModelEndpointConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    sampling: SamplingConfig = Field(default_factory=SamplingConfig)


class ScopeRef(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scope_type: ScopeType
    scope_id: str = Field(min_length=1)


class VerificationPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')

    checklist: tuple[str, ...] = Field(min_length=1)


class TaskEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')

    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    parent_task_id: str | None = None
    trace_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    parent_instruction: str | None = None
    scope: tuple[str, ...] = Field(min_length=1)
    dod: tuple[str, ...] = Field(min_length=1)
    verification: VerificationPlan
    confirmation_gate: bool = False   # if True, pause after this task for human approval


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    envelope: TaskEnvelope
    status: TaskStatus = TaskStatus.CREATED
    assigned_instance_id: str | None = None
    result: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RoleDefinition(BaseModel):
    model_config = ConfigDict(extra='forbid')

    role_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capabilities: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    model_profile: str = Field(default='default')
    system_prompt: str = Field(min_length=1)


class SubAgentInstance(BaseModel):
    model_config = ConfigDict(extra='forbid')

    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    status: InstanceStatus = InstanceStatus.IDLE
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_active_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    completed_tasks: int = 0
    failed_tasks: int = 0


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')

    event_type: EventType
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str | None = None
    instance_id: str | None = None
    payload_json: str = Field(default='{}')
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class IntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str | None = None
    intent: str = Field(min_length=1)
    parent_instruction: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    confirmation_gate: bool = False   # if True, every sub-task pauses for human approval after completion


class RunResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    trace_id: str
    root_task_id: str
    status: Literal['completed', 'failed']
    output: str


class StateMutation(BaseModel):
    model_config = ConfigDict(extra='forbid')

    scope: ScopeRef
    key: str = Field(min_length=1)
    value_json: str = Field(min_length=1)


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    task_id: str
    passed: bool
    details: tuple[str, ...]


class InjectionMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_id: str = Field(min_length=1)
    recipient_instance_id: str = Field(min_length=1)
    source: InjectionSource
    content: str = Field(min_length=1)
    sender_instance_id: str | None = None
    sender_role_id: str | None = None
    priority: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class RunEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')

    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    task_id: str | None = None
    event_type: RunEventType
    payload_json: str = Field(default='{}')
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


class AgentRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')

    run_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    status: InstanceStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
