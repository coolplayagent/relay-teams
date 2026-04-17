from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue

from relay_teams.hooks.hook_models import HookEventName


class HookEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_name: HookEventName
    session_id: str
    run_id: str
    trace_id: str
    task_id: str | None = None
    instance_id: str | None = None
    role_id: str | None = None
    session_mode: str = ""
    run_kind: str = ""


class SessionStartInput(HookEventInput):
    workspace_id: str = ""


class SessionEndInput(HookEventInput):
    status: str = ""
    completion_reason: str = ""
    output_text: str = ""


class UserPromptSubmitInput(HookEventInput):
    user_prompt: str = ""
    input_parts: tuple[dict[str, JsonValue], ...] = ()


class PreToolUseInput(HookEventInput):
    tool_name: str
    tool_call_id: str
    tool_input: dict[str, JsonValue]


class PermissionRequestInput(HookEventInput):
    tool_name: str
    tool_call_id: str
    tool_input: dict[str, JsonValue]
    approval_required: bool = True


class PostToolUseInput(HookEventInput):
    tool_name: str
    tool_call_id: str
    tool_input: dict[str, JsonValue]
    tool_result: dict[str, JsonValue]


class PostToolUseFailureInput(HookEventInput):
    tool_name: str
    tool_call_id: str
    tool_input: dict[str, JsonValue]
    tool_error: dict[str, JsonValue]


class StopInput(HookEventInput):
    completion_reason: str = ""
    output_text: str = ""


class StopFailureInput(HookEventInput):
    completion_reason: str = ""
    error_code: str = ""
    error_message: str = ""
