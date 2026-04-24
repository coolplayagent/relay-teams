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
    start_reason: str = ""


class SessionEndInput(HookEventInput):
    status: str = ""
    end_reason: str = ""
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


class SubagentStartInput(HookEventInput):
    parent_run_id: str = ""
    subagent_run_id: str
    subagent_task_id: str
    subagent_instance_id: str
    subagent_role_id: str
    subagent_type: str = ""
    title: str = ""
    prompt: str = ""


class SubagentStopInput(HookEventInput):
    parent_run_id: str = ""
    subagent_run_id: str
    subagent_task_id: str
    subagent_instance_id: str
    subagent_role_id: str
    subagent_type: str = ""
    title: str = ""
    status: str = ""
    output_text: str = ""


class TaskCreatedInput(HookEventInput):
    created_task_id: str
    parent_task_id: str | None = None
    title: str = ""
    objective: str = ""


class TaskCompletedInput(HookEventInput):
    completed_task_id: str
    title: str = ""
    objective: str = ""
    output_text: str = ""
    completion_reason: str = ""


class PreCompactInput(HookEventInput):
    conversation_id: str
    compact_trigger: str = ""
    message_count_before: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after_microcompact: int = 0
    threshold_tokens: int = 0
    target_tokens: int = 0


class PostCompactInput(HookEventInput):
    conversation_id: str
    compact_trigger: str = ""
    message_count_before: int = 0
    message_count_after: int = 0
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
