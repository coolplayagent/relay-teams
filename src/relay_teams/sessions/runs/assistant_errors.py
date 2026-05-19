from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, JsonValue
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


class RunErrorPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    error_code: str = ""
    user_message: str
    recovery_prompt: str | None = None
    diagnostic_message: str = ""


INVALID_TOOL_ARGS_RECOVERY_MESSAGE = (
    "The previous tool call arguments were not valid JSON. "
    "Do not repeat already successful tool calls. "
    "Continue from the latest successful tool results already in the conversation. "
    "If you call a tool again, output strict JSON only, with double-quoted property names "
    "and arguments that exactly match the tool schema."
)
NETWORK_EXCEPTION_RECOVERY_MESSAGE = (
    "The previous model request could not complete because of a transient network or transport failure. "
    "Continue from the latest successful conversation state already persisted. "
    "Do not repeat already successful tool calls or restate text that has already been sent. "
    "If prior work is incomplete, continue from the last confirmed point."
)


def build_auto_recovery_prompt(error_code: str | None) -> str | None:
    code = str(error_code or "").strip().lower()
    if code == "model_tool_args_invalid_json":
        return INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    if code in {"network_stream_interrupted", "network_timeout", "network_error"}:
        return NETWORK_EXCEPTION_RECOVERY_MESSAGE
    return None


def _append_error_detail(message: str, detail: str) -> str:
    normalized_detail = detail.strip()
    if not normalized_detail:
        return message
    return f"{message} Details: {normalized_detail}"


def _build_user_error_message(
    error_code: str,
    *,
    detail: str = "",
) -> str:
    if error_code == "model_tool_args_invalid_json":
        return (
            "The model returned invalid tool call arguments. "
            "The run can continue after the arguments are corrected."
        )
    if error_code == "network_stream_interrupted":
        return (
            "The model response stream was interrupted by a temporary network or transport failure. "
            "Retry the run to continue."
        )
    if error_code == "network_timeout":
        return (
            "The model request timed out while waiting for the provider to respond. "
            "Check provider latency, proxy settings, base_url, or increase the configured timeout."
        )
    if error_code == "network_error":
        return (
            "The model request failed before a usable response was received. "
            "Check DNS, outbound network connectivity, proxy or NO_PROXY settings, and base_url."
        )
    if error_code == "proxy_blocked":
        return (
            "The model request reached an enterprise proxy block page instead of the model endpoint. "
            "Check base_url, proxy routing, proxy credentials, and NO_PROXY entries for the model host."
        )
    if error_code == "auth_invalid":
        return (
            "The model provider rejected the request because the API key is invalid. "
            "Check the current model profile credentials and try again."
        )
    if error_code == "verification_failed":
        return _append_error_detail(
            "The task verification did not pass. "
            "Review the task spec and evidence expectations, then continue with corrected output.",
            detail,
        )
    if error_code == "incomplete_todos":
        return (
            "The request could not be marked complete because run-scoped todos "
            "are still incomplete. Reconcile the todo list with actual progress "
            "before finalizing."
        )
    lowered = detail.lower()
    if "prompt is too long" in lowered:
        return (
            "The model request prompt is too long. Reduce the input or context size, "
            "then try again."
        )
    if "credit balance is too low" in lowered:
        return (
            "The model provider rejected the request because the API credit balance is too low. "
            "Add credit or switch to another configured model profile."
        )
    if "x-api-key" in lowered or "api key" in lowered:
        return (
            "The model provider rejected the request because the API key is invalid. "
            "Check the current model profile credentials and try again."
        )
    if detail:
        return (
            "The request could not be completed because of an API or execution error. "
            f"Details: {detail}"
        )
    return "The request could not be completed because of an API or execution error."


def build_error_presentation(
    *,
    error_code: str | None,
    error_message: str | None,
) -> RunErrorPresentation:
    code = str(error_code or "").strip().lower()
    detail = str(error_message or "").strip()
    return RunErrorPresentation(
        error_code=code,
        user_message=_build_user_error_message(code, detail=detail),
        recovery_prompt=build_auto_recovery_prompt(code),
        diagnostic_message=detail,
    )


def build_assistant_error_message(
    *,
    error_code: str | None,
    error_message: str | None,
) -> str:
    return build_error_presentation(
        error_code=error_code,
        error_message=error_message,
    ).user_message


def build_tool_error_result(
    *,
    error_code: str,
    message: str,
) -> dict[str, JsonValue]:
    return {
        "ok": False,
        "error": {
            "code": error_code,
            "message": message,
        },
    }


def build_assistant_error_response(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
