from __future__ import annotations

from relay_teams.sessions.runs.assistant_errors import (
    INVALID_TOOL_ARGS_RECOVERY_MESSAGE,
    NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE,
    build_assistant_error_message,
    build_auto_recovery_prompt,
)


def test_build_auto_recovery_prompt_reuses_invalid_tool_args_guidance() -> None:
    assert (
        build_auto_recovery_prompt("model_tool_args_invalid_json")
        == INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    )
    assert (
        build_assistant_error_message(
            error_code="model_tool_args_invalid_json",
            error_message=None,
        )
        == INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    )


def test_build_auto_recovery_prompt_reuses_network_stream_guidance() -> None:
    assert (
        build_auto_recovery_prompt("network_stream_interrupted")
        == NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    )
    assert (
        build_assistant_error_message(
            error_code="network_stream_interrupted",
            error_message=None,
        )
        == NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    )
    assert (
        build_assistant_error_message(
            error_code="network_timeout",
            error_message=None,
        )
        == NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    )
    assert build_auto_recovery_prompt("network_timeout") is None


def test_build_assistant_error_message_uses_auth_invalid_code() -> None:
    message = build_assistant_error_message(
        error_code="auth_invalid",
        error_message="provider rejected request status_code: 401",
    )

    assert "API key is invalid" in message
