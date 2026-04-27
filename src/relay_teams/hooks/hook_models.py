from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)


class HookEventName(str, Enum):
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    PERMISSION_REQUEST = "PermissionRequest"
    PERMISSION_DENIED = "PermissionDenied"
    POST_TOOL_USE = "PostToolUse"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"
    STOP = "Stop"
    STOP_FAILURE = "StopFailure"
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"


class HookHandlerType(str, Enum):
    COMMAND = "command"
    HTTP = "http"
    PROMPT = "prompt"
    AGENT = "agent"


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
    ROLE = "role"
    SKILL = "skill"


class HookOnError(str, Enum):
    IGNORE = "ignore"
    FAIL = "fail"


class HookShell(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"


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
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: HookHandlerType
    name: str = ""
    if_rule: str | None = Field(
        default=None,
        validation_alias=AliasChoices("if", "if_rule"),
        serialization_alias="if",
    )
    timeout_seconds: float = Field(
        default=5.0,
        gt=0.0,
        le=600.0,
        validation_alias=AliasChoices("timeout_seconds", "timeout"),
        serialization_alias="timeout",
    )
    run_async: bool = Field(
        default=False,
        validation_alias=AliasChoices("run_async", "async"),
        serialization_alias="async",
    )
    on_error: HookOnError = HookOnError.IGNORE
    command: str | None = None
    shell: HookShell | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    allowed_env_vars: tuple[str, ...] = ()
    prompt: str | None = None
    model: str | None = None
    role_id: str | None = None
    async_rewake: bool = False
    status_message: Optional[str] = None

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "HookHandlerConfig":
        if self.type == HookHandlerType.COMMAND:
            if not str(self.command or "").strip():
                raise ValueError("command hook requires command")
            return self
        if self.type == HookHandlerType.HTTP:
            if not str(self.url or "").strip():
                raise ValueError("http hook requires url")
            return self
        if self.type == HookHandlerType.PROMPT:
            if not str(self.prompt or "").strip():
                raise ValueError("prompt hook requires prompt")
            return self
        if not str(self.prompt or "").strip():
            raise ValueError("agent hook requires prompt")
        return self


class HookMatcherGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    matcher: str = "*"
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
    if_rule: str | None = Field(default=None, serialization_alias="if")
    role_ids: tuple[str, ...] = ()
    session_modes: tuple[str, ...] = ()
    run_kinds: tuple[str, ...] = ()
    timeout_seconds: float = 5.0
    run_async: bool = False
    on_error: HookOnError = HookOnError.IGNORE
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
