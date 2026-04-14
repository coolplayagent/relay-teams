from __future__ import annotations

from enum import Enum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic import JsonValue


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
    CONTINUE = "continue"
    RETRY = "retry"


class HookHandlerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: HookHandlerType
    name: str = Field(default="", min_length=0)
    command: str = ""
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=5.0, gt=0.0, le=120.0)


class HookMatcherGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    matcher: str = "*"
    hooks: tuple[HookHandlerConfig, ...] = ()


class HooksConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hooks: dict[HookEventName, tuple[HookMatcherGroup, ...]] = Field(
        default_factory=dict
    )

    def groups_for(self, event_name: HookEventName) -> tuple[HookMatcherGroup, ...]:
        return self.hooks.get(event_name, ())


class HookDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HookDecisionType
    reason: str = ""
    updated_input: str | None = None
    additional_context: str = ""
    set_env: dict[str, str] = Field(default_factory=dict)


class HookDecisionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HookDecisionType
    reason: str = ""
    updated_input: str | None = None
    additional_context: str = ""
    set_env: dict[str, str] = Field(default_factory=dict)
    matched_handlers: int = 0


class SessionHookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    status: str = ""
    completion_reason: str = ""


class UserPromptSubmitHookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    prompt: str


class StopHookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    root_task_id: str
    status: str
    completion_reason: str
    output_text: str = ""
    error_message: str = ""


class ToolHookInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    run_id: str
    trace_id: str
    task_id: str
    instance_id: str
    role_id: str
    tool_name: str
    tool_call_id: str
    tool_input: dict[str, JsonValue] = Field(default_factory=dict)


def parse_hook_decision_payload(value: object) -> HookDecision | None:
    if not isinstance(value, dict):
        return None
    payload = cast(dict[object, object], value)
    raw_specific = payload.get("hookSpecificOutput")
    if isinstance(raw_specific, dict):
        nested = cast(dict[object, object], raw_specific)
        raw_decision = nested.get("decision") or nested.get("permissionDecision")
        if isinstance(raw_decision, str) and raw_decision.strip():
            return HookDecision(
                decision=HookDecisionType(raw_decision.strip()),
                reason=_string_field(
                    nested.get("reason") or nested.get("permissionDecisionReason")
                ),
                updated_input=_optional_string_field(
                    nested.get("updated_input") or nested.get("updatedInput")
                ),
                additional_context=_string_field(
                    nested.get("additional_context") or nested.get("additionalContext")
                ),
                set_env=_string_map_field(
                    nested.get("set_env") or nested.get("setEnv")
                ),
            )
    raw_decision = payload.get("decision")
    if not isinstance(raw_decision, str) or not raw_decision.strip():
        return None
    return HookDecision(
        decision=HookDecisionType(raw_decision.strip()),
        reason=_string_field(payload.get("reason")),
        updated_input=_optional_string_field(
            payload.get("updated_input") or payload.get("updatedInput")
        ),
        additional_context=_string_field(
            payload.get("additional_context") or payload.get("additionalContext")
        ),
        set_env=_string_map_field(payload.get("set_env") or payload.get("setEnv")),
    )


def _string_field(value: object) -> str:
    if isinstance(value, str):
        return value
    return ""


def _optional_string_field(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _string_map_field(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(item, str):
            continue
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        result[normalized_key] = item
    return result
