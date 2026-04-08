from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelResponse, TextPart


class RunCompletionReason(str, Enum):
    ASSISTANT_RESPONSE = "assistant_response"
    ASSISTANT_ERROR = "assistant_error"
    STOPPED_BY_USER = "stopped_by_user"


class AssistantRunErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: str
    session_id: str
    task_id: str
    instance_id: str
    role_id: str
    conversation_id: str
    assistant_message: str
    error_code: str = ""
    error_message: str = ""


class AssistantRunError(RuntimeError):
    def __init__(self, payload: AssistantRunErrorPayload) -> None:
        self.payload = payload
        super().__init__(payload.error_message or payload.assistant_message)


INVALID_TOOL_ARGS_RECOVERY_MESSAGE = (
    "The previous tool call arguments were not valid JSON. "
    "Do not repeat already successful tool calls. "
    "Continue from the latest successful tool results already in the conversation. "
    "If you call a tool again, output strict JSON only, with double-quoted property names "
    "and arguments that exactly match the tool schema."
)
NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE = (
    "The previous model stream was interrupted by a transient network or transport failure. "
    "Continue from the latest successful conversation state already persisted. "
    "Do not repeat already successful tool calls or restate text that has already been sent. "
    "If prior work is incomplete, continue from the last confirmed point."
)


def build_auto_recovery_prompt(error_code: str | None) -> str | None:
    code = str(error_code or "").strip().lower()
    if code == "model_tool_args_invalid_json":
        return INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    if code == "network_stream_interrupted":
        return NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    return None


def _build_recovery_guidance_message(error_code: str) -> str | None:
    if error_code == "model_tool_args_invalid_json":
        return INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    if error_code in {
        "network_stream_interrupted",
        "network_timeout",
        "network_error",
    }:
        return NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    if error_code == "auth_invalid":
        return (
            "The previous request could not continue because the API key is invalid. "
            "The conversation state already persisted is still valid."
        )
    return None


def build_assistant_error_message(
    *,
    error_code: str | None,
    error_message: str | None,
) -> str:
    code = str(error_code or "").strip().lower()
    detail = str(error_message or "").strip()

    recovery_guidance = _build_recovery_guidance_message(code)
    if recovery_guidance is not None:
        return recovery_guidance
    lowered = detail.lower()
    if "prompt is too long" in lowered:
        return (
            "The previous request could not continue because the prompt is too long. "
            "Continue from the latest persisted conversation state and keep the next response focused."
        )
    if "credit balance is too low" in lowered:
        return (
            "The previous request could not continue because the API credit balance is too low. "
            "The conversation state already persisted is still valid."
        )
    if "x-api-key" in lowered or "api key" in lowered:
        return (
            "The previous request could not continue because the API key is invalid. "
            "The conversation state already persisted is still valid."
        )
    if detail:
        return (
            "The previous request could not be completed because of an API or execution error. "
            "Continue from the latest successful conversation state already persisted. "
            "Do not repeat already successful tool calls. "
            f"Details: {detail}"
        )
    return (
        "The previous request could not be completed because of an API or execution error. "
        "Continue from the latest successful conversation state already persisted. "
        "Do not repeat already successful tool calls."
    )


def build_tool_error_result(
    *,
    error_code: str,
    message: str,
) -> dict[str, object]:
    return {
        "ok": False,
        "error": {
            "code": error_code,
            "message": message,
        },
    }


def build_assistant_error_response(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
