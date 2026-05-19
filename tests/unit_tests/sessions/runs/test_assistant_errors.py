from __future__ import annotations

import pytest

from relay_teams.sessions.runs.assistant_errors import (
    INVALID_TOOL_ARGS_RECOVERY_MESSAGE,
    NETWORK_EXCEPTION_RECOVERY_MESSAGE,
    build_assistant_error_message,
    build_auto_recovery_prompt,
    build_error_presentation,
)


def test_build_auto_recovery_prompt_reuses_invalid_tool_args_guidance() -> None:
    assert (
        build_auto_recovery_prompt("model_tool_args_invalid_json")
        == INVALID_TOOL_ARGS_RECOVERY_MESSAGE
    )
    message = build_assistant_error_message(
        error_code="model_tool_args_invalid_json",
        error_message=None,
    )

    assert "invalid tool call arguments" in message
    assert "Do not repeat already successful tool calls" not in message


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
    assert "Retry the run to continue" in stream_message
    assert "latest saved conversation state" not in stream_message
    assert "incomplete chunked read" not in stream_message

    assert "timed out while waiting for the provider to respond" in timeout_message
    assert "increase the configured timeout" in timeout_message
    assert "Details:" not in timeout_message

    assert "before a usable response was received" in network_message
    assert "DNS" in network_message
    assert "connection refused" not in network_message


def test_build_assistant_error_message_uses_auth_invalid_code() -> None:
    message = build_assistant_error_message(
        error_code="auth_invalid",
        error_message="provider rejected request status_code: 401",
    )

    assert "API key is invalid" in message
    assert "conversation state" not in message
    assert "persisted" not in message


def test_build_assistant_error_message_includes_full_proxy_block_detail() -> None:
    detail = (
        "status_code: 403\n"
        "model_name: deepseek-v4-flash\n"
        "body:\n"
        '<html><head><meta name="keywords" content="SWG,Proxy,NetentSec" /></head></html>'
    )

    message = build_assistant_error_message(
        error_code="proxy_blocked",
        error_message=detail,
    )

    assert "enterprise proxy block page" in message
    assert "```text" not in message
    assert detail not in message


def test_build_assistant_error_message_uses_incomplete_todos_guidance() -> None:
    message = build_assistant_error_message(
        error_code="incomplete_todos",
        error_message="You attempted to finish while run-scoped todos are still incomplete.",
    )

    assert "could not be marked complete" in message
    assert "run-scoped todos are still incomplete" in message
    assert "Reconcile the todo list" in message
    assert "Do not repeat already successful tool calls" not in message
    assert "persisted" not in message
    assert "API or execution error" not in message


def test_build_assistant_error_message_uses_verification_failed_guidance() -> None:
    message = build_assistant_error_message(
        error_code="verification_failed",
        error_message="Contract check failed.",
    )

    assert "verification did not pass" in message
    assert "Review the task spec and evidence expectations" in message
    assert "Contract check failed" in message
    assert "API or execution error" not in message


def test_build_assistant_error_message_verification_failed_without_detail() -> None:
    message = build_assistant_error_message(
        error_code="verification_failed",
        error_message=None,
    )

    assert "verification did not pass" in message
    assert "Review the task spec and evidence expectations" in message
    assert "API or execution error" not in message


def test_build_error_presentation_separates_user_recovery_and_diagnostics() -> None:
    presentation = build_error_presentation(
        error_code="network_timeout",
        error_message="provider request timed out after 30 seconds",
    )

    assert presentation.error_code == "network_timeout"
    assert "timed out while waiting for the provider" in presentation.user_message
    assert "conversation state already persisted" not in presentation.user_message
    assert presentation.recovery_prompt == NETWORK_EXCEPTION_RECOVERY_MESSAGE
    assert (
        presentation.diagnostic_message == "provider request timed out after 30 seconds"
    )


def test_build_error_presentation_keeps_auth_error_user_facing_only() -> None:
    presentation = build_error_presentation(
        error_code="auth_invalid",
        error_message="provider rejected request status_code: 401",
    )

    assert "API key is invalid" in presentation.user_message
    assert "conversation state" not in presentation.user_message
    assert presentation.recovery_prompt is None
    assert (
        presentation.diagnostic_message == "provider rejected request status_code: 401"
    )


@pytest.mark.parametrize(
    ("error_code", "error_message", "expected_fragment"),
    [
        (
            "task_timeout",
            "Task timed out after 120s",
            "API or execution error",
        ),
        (
            "internal_execution_error",
            "worker crashed",
            "API or execution error",
        ),
        (
            "orchestration_cycles_exhausted",
            "2 delegated task(s) are still pending.",
            "API or execution error",
        ),
        (
            "delegated_task_execution_disabled",
            "Delegated task execution is disabled by policy.",
            "API or execution error",
        ),
        (
            "run_start_failed",
            "startup failed",
            "API or execution error",
        ),
        (
            "run_worker_failed",
            "worker failed",
            "API or execution error",
        ),
        (
            "",
            "prompt is too long for this model",
            "prompt is too long",
        ),
        (
            "",
            "credit balance is too low",
            "credit balance is too low",
        ),
        (
            "",
            "x-api-key header is invalid",
            "API key is invalid",
        ),
        (
            "",
            "",
            "API or execution error",
        ),
    ],
)
def test_build_error_presentation_keeps_known_terminal_errors_user_facing(
    error_code: str,
    error_message: str,
    expected_fragment: str,
) -> None:
    presentation = build_error_presentation(
        error_code=error_code,
        error_message=error_message,
    )

    assert expected_fragment in presentation.user_message
    assert "conversation state" not in presentation.user_message
    assert "persisted" not in presentation.user_message
    assert (
        "Do not repeat already successful tool calls" not in presentation.user_message
    )
    assert presentation.recovery_prompt is None
    assert presentation.diagnostic_message == error_message
