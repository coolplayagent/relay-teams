from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class HookEventName(str, Enum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"


class HookHandlerType(str, Enum):
    COMMAND = "command"
    HTTP = "http"


class HookDecisionType(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    UPDATED_INPUT = "updated_input"
    ADDITIONAL_CONTEXT = "additional_context"
    CONTINUE = "continue"
    RETRY = "retry"
    SET_ENV = "set_env"
    DEFER = "defer"
    OBSERVE = "observe"


class HookExecutionStatus(str, Enum):
    MATCHED = "matched"
    COMPLETED = "completed"
    FAILED = "failed"


class HookSourceScope(str, Enum):
    USER = "user"
    PROJECT = "project"
    PROJECT_LOCAL = "project_local"


class HookSourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: HookSourceScope
    path: Path


class HookDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: HookDecisionType
    reason: str = ""
    updated_input: JsonValue | None = None
    additional_context: tuple[str, ...] = ()
    set_env: dict[str, str] = Field(default_factory=dict)
    deferred_action: str = ""


class HookHandlerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: HookHandlerType
    name: str = ""
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    run_async: bool = False
    on_error: str = "ignore"
    command: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)


class HookMatcherGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matcher: str = "*"
    if_condition: str | None = None
    tool_names: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()
    session_modes: tuple[str, ...] = ()
    run_kinds: tuple[str, ...] = ()
    hooks: tuple[HookHandlerConfig, ...] = ()


class HooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = Field(
        default_factory=dict
    )


class ResolvedHookMatcherGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: HookSourceInfo
    event_name: HookEventName
    group: HookMatcherGroup


class HookRuntimeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[HookSourceInfo, ...] = ()
    hooks: dict[HookEventName, tuple[ResolvedHookMatcherGroup, ...]] = Field(
        default_factory=dict
    )


class LoadedHookRuntimeView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    handler_type: HookHandlerType
    event_name: HookEventName
    matcher: str = "*"
    if_condition: str | None = None
    tool_names: tuple[str, ...] = ()
    role_ids: tuple[str, ...] = ()
    session_modes: tuple[str, ...] = ()
    run_kinds: tuple[str, ...] = ()
    timeout_seconds: float = 5.0
    run_async: bool = False
    on_error: str = "ignore"
    source: HookSourceInfo


class HookRuntimeView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[HookSourceInfo, ...] = ()
    loaded_hooks: tuple[LoadedHookRuntimeView, ...] = ()


class HookExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: HookSourceInfo
    event_name: HookEventName
    handler_name: str
    handler_type: HookHandlerType
    status: HookExecutionStatus
    decision: HookDecision | None = None
    duration_ms: int = 0
    error: str = ""


class HookDecisionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HookDecisionType
    reason: str = ""
    updated_input: JsonValue | None = None
    additional_context: tuple[str, ...] = ()
    set_env: dict[str, str] = Field(default_factory=dict)
    deferred_action: str = ""
    executions: tuple[HookExecutionResult, ...] = ()
