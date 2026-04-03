from __future__ import annotations

from agent_teams.sessions.runs.assistant_errors import (
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


def test_build_auto_recovery_prompt_uses_network_stream_guidance_only() -> None:
    assert (
        build_auto_recovery_prompt("network_stream_interrupted")
        == NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    )
    message = build_assistant_error_message(
        error_code="network_stream_interrupted",
        error_message=None,
    )
    assert message != NETWORK_STREAM_INTERRUPTED_RECOVERY_MESSAGE
    assert "interrupted before it finished" in message
    assert "Please retry your last message." in message
    assert build_auto_recovery_prompt("network_timeout") is None


def test_build_assistant_error_message_uses_user_facing_network_messages() -> None:
    timeout_message = build_assistant_error_message(
        error_code="network_timeout",
        error_message=None,
    )
    network_message = build_assistant_error_message(
        error_code="network_error",
        error_message="connection reset by peer",
    )

    assert "timed out before it finished" in timeout_message
    assert "Please retry your last message." in timeout_message
    assert "network connection problem" in network_message
    assert "connection reset by peer" in network_message


def test_build_assistant_error_message_uses_auth_invalid_code() -> None:
    message = build_assistant_error_message(
        error_code="auth_invalid",
        error_message="provider rejected request status_code: 401",
    )

    assert "API key is invalid" in message
