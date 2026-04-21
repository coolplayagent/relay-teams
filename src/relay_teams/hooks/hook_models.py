from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


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
    SUBAGENT_START = "SubagentStart"
    SUBAGENT_STOP = "SubagentStop"
    TASK_CREATED = "TaskCreated"
    TASK_COMPLETED = "TaskCompleted"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"


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
    prompt: str | None = None
    role_id: str | None = None
    model_profile: str | None = None

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> HookHandlerConfig:
        if self.type == HookHandlerType.COMMAND and not str(self.command or "").strip():
            raise ValueError("Command hook requires a command")
        if self.type == HookHandlerType.HTTP and not str(self.url or "").strip():
            raise ValueError("HTTP hook requires a url")
        if self.type == HookHandlerType.PROMPT and not str(self.prompt or "").strip():
            raise ValueError("Prompt hook requires a prompt")
        if self.type == HookHandlerType.AGENT and not str(self.role_id or "").strip():
            raise ValueError("Agent hook requires a role_id")
        return self


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
    role_id: str | None = None
    model_profile: str | None = None
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


_EVENT_ALLOWED_HANDLER_TYPES: dict[HookEventName, frozenset[HookHandlerType]] = {
    HookEventName.SESSION_START: frozenset({HookHandlerType.COMMAND}),
    HookEventName.SESSION_END: frozenset(
        {HookHandlerType.COMMAND, HookHandlerType.HTTP}
    ),
    HookEventName.USER_PROMPT_SUBMIT: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.PRE_TOOL_USE: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.PERMISSION_REQUEST: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.POST_TOOL_USE: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.POST_TOOL_USE_FAILURE: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.STOP: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.STOP_FAILURE: frozenset(
        {HookHandlerType.COMMAND, HookHandlerType.HTTP}
    ),
    HookEventName.SUBAGENT_START: frozenset(
        {HookHandlerType.COMMAND, HookHandlerType.HTTP}
    ),
    HookEventName.SUBAGENT_STOP: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.TASK_CREATED: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.TASK_COMPLETED: frozenset(
        {
            HookHandlerType.COMMAND,
            HookHandlerType.HTTP,
            HookHandlerType.PROMPT,
            HookHandlerType.AGENT,
        }
    ),
    HookEventName.PRE_COMPACT: frozenset(
        {HookHandlerType.COMMAND, HookHandlerType.HTTP}
    ),
    HookEventName.POST_COMPACT: frozenset(
        {HookHandlerType.COMMAND, HookHandlerType.HTTP}
    ),
}

_EVENT_ALLOWED_DECISIONS: dict[HookEventName, frozenset[HookDecisionType]] = {
    HookEventName.SESSION_START: frozenset(
        {HookDecisionType.ALLOW, HookDecisionType.SET_ENV}
    ),
    HookEventName.SESSION_END: frozenset({HookDecisionType.OBSERVE}),
    HookEventName.USER_PROMPT_SUBMIT: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.UPDATED_INPUT,
            HookDecisionType.ADDITIONAL_CONTEXT,
        }
    ),
    HookEventName.PRE_TOOL_USE: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.ASK,
            HookDecisionType.UPDATED_INPUT,
            HookDecisionType.DEFER,
        }
    ),
    HookEventName.PERMISSION_REQUEST: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.ASK,
        }
    ),
    HookEventName.POST_TOOL_USE: frozenset(
        {
            HookDecisionType.CONTINUE,
            HookDecisionType.ADDITIONAL_CONTEXT,
        }
    ),
    HookEventName.POST_TOOL_USE_FAILURE: frozenset(
        {
            HookDecisionType.CONTINUE,
            HookDecisionType.ADDITIONAL_CONTEXT,
        }
    ),
    HookEventName.STOP: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.RETRY,
            HookDecisionType.ADDITIONAL_CONTEXT,
        }
    ),
    HookEventName.STOP_FAILURE: frozenset({HookDecisionType.OBSERVE}),
    HookEventName.SUBAGENT_START: frozenset({HookDecisionType.OBSERVE}),
    HookEventName.SUBAGENT_STOP: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.OBSERVE,
        }
    ),
    HookEventName.TASK_CREATED: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.OBSERVE,
        }
    ),
    HookEventName.TASK_COMPLETED: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.OBSERVE,
        }
    ),
    HookEventName.PRE_COMPACT: frozenset(
        {
            HookDecisionType.ALLOW,
            HookDecisionType.DENY,
            HookDecisionType.OBSERVE,
        }
    ),
    HookEventName.POST_COMPACT: frozenset({HookDecisionType.OBSERVE}),
}


def event_allows_handler_type(
    event_name: HookEventName, handler_type: HookHandlerType
) -> bool:
    return handler_type in _EVENT_ALLOWED_HANDLER_TYPES[event_name]


def event_allows_decision(
    event_name: HookEventName, decision: HookDecisionType
) -> bool:
    return decision in _EVENT_ALLOWED_DECISIONS[event_name]
