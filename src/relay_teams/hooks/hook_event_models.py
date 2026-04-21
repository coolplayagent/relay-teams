from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

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
    workspace_id: str = ""
    conversation_id: str = ""
    session_mode: str = ""
    run_kind: str = ""


class SessionStartInput(HookEventInput):
    source: str = ""
    model: str = ""
    agent_type: str | None = None


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


class TaskCreatedInput(HookEventInput):
    parent_task_id: str | None = None
    task_title: str = ""
    task_objective: str = ""


class TaskCompletedInput(HookEventInput):
    parent_task_id: str | None = None
    task_title: str = ""
    task_objective: str = ""
    status: str = ""
    output_text: str = ""
    error_message: str = ""


class SubagentStartInput(HookEventInput):
    parent_run_id: str | None = None
    subagent_title: str = ""
    objective: str = ""


class SubagentStopInput(HookEventInput):
    parent_run_id: str | None = None
    subagent_title: str = ""
    status: str = ""
    output_text: str = ""


class PreCompactInput(HookEventInput):
    conversation_id: str = ""
    message_count: int = Field(default=0, ge=0)
    estimated_tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after_microcompact: int = Field(default=0, ge=0)
    history_trigger_tokens: int = Field(default=0, ge=0)
    history_target_tokens: int = Field(default=0, ge=0)


class PostCompactInput(HookEventInput):
    conversation_id: str = ""
    message_count_before: int = Field(default=0, ge=0)
    message_count_after: int = Field(default=0, ge=0)
    compacted_message_count: int = Field(default=0, ge=0)
    applied: bool = False
