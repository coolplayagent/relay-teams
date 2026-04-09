from __future__ import annotations

from relay_teams.sessions.runs.assistant_errors import (
    INVALID_TOOL_ARGS_RECOVERY_MESSAGE,
    NETWORK_EXCEPTION_RECOVERY_MESSAGE,
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
        == NETWORK_EXCEPTION_RECOVERY_MESSAGE
    )
    assert (
        build_auto_recovery_prompt("network_timeout")
        == NETWORK_EXCEPTION_RECOVERY_MESSAGE
    )
    assert (
        build_auto_recovery_prompt("network_error")
        == NETWORK_EXCEPTION_RECOVERY_MESSAGE
    )


def test_build_assistant_error_message_uses_specific_network_messages() -> None:
    stream_message = build_assistant_error_message(
        error_code="network_stream_interrupted",
        error_message="incomplete chunked read",
    )
    timeout_message = build_assistant_error_message(
        error_code="network_timeout",
        error_message="request timed out",
    )
    network_message = build_assistant_error_message(
        error_code="network_error",
        error_message="connection refused",
    )

    assert "response stream was interrupted" in stream_message
    assert (
        "Retry to continue from the latest saved conversation state" in stream_message
    )
    assert "incomplete chunked read" in stream_message

    assert "timed out while waiting for the provider to respond" in timeout_message
    assert "responding slowly" in timeout_message
    assert "request timed out" in timeout_message

    assert "before a usable response was received" in network_message
    assert "DNS resolution" in network_message
    assert "connection refused" in network_message


def test_build_assistant_error_message_uses_auth_invalid_code() -> None:
    message = build_assistant_error_message(
        error_code="auth_invalid",
        error_message="provider rejected request status_code: 401",
    )

    assert "API key is invalid" in message
