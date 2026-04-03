# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime

from agent_teams.agents.execution.llm_session import AgentLlmSession
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.llm_retry import LlmRetryErrorInfo
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
)


def test_maybe_enrich_tool_result_payload_wraps_builtin_computer_results() -> None:
    session = object.__new__(AgentLlmSession)
    session._mcp_registry = McpRegistry()

    payload = AgentLlmSession._maybe_enrich_tool_result_payload(
        session,
        tool_name="capture_screen",
        result_payload={"ok": True, "data": {"text": "Captured."}},
    )

    assert isinstance(payload, dict)
    assert payload["ok"] is True
    data = payload["data"]
    assert isinstance(data, dict)
    computer = data["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "tool"
    assert computer["runtime_kind"] == "builtin_tool"


def test_maybe_enrich_tool_result_payload_wraps_session_mcp_results() -> None:
    session = object.__new__(AgentLlmSession)
    session._mcp_registry = McpRegistry(
        (
            McpServerSpec(
                name="desktop",
                config={},
                server_config={"transport": "stdio", "command": "desktop-mcp"},
                source=McpConfigScope.SESSION,
            ),
        )
    )

    payload = AgentLlmSession._maybe_enrich_tool_result_payload(
        session,
        tool_name="desktop_click",
        result_payload={"text": "Clicked."},
    )

    assert isinstance(payload, dict)
    computer = payload["computer"]
    assert isinstance(computer, dict)
    assert computer["source"] == "mcp"
    assert computer["runtime_kind"] == "session_mcp_acp"


def test_normalize_tool_call_args_for_replay_updates_live_messages() -> None:
    response = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="shell",
                args=(
                    '{"command":"python -c "print(\'hello\')""'
                    ',"background":true,"yield_time_ms":null}'
                ),
                tool_call_id="call-live",
            )
        ]
    )

    AgentLlmSession._normalize_tool_call_args_for_replay([response])

    tool_call = response.parts[0]
    assert isinstance(tool_call, ToolCallPart)
    assert isinstance(tool_call.args, str)
    assert json.loads(tool_call.args) == {
        "command": "python -c \"print('hello')\"",
        "background": True,
        "yield_time_ms": None,
    }


def test_should_retry_after_text_side_effect_rejects_transport_errors() -> None:
    session = object.__new__(AgentLlmSession)

    should_retry = AgentLlmSession._should_retry_after_text_side_effect(
        session,
        retry_error=LlmRetryErrorInfo(
            message="incomplete chunked read",
            error_code="network_stream_interrupted",
            retryable=True,
            transport_error=True,
        ),
    )

    assert should_retry is False


def test_should_pause_for_recoverable_error_requires_transport_side_effects() -> None:
    session = object.__new__(AgentLlmSession)
    retry_error = LlmRetryErrorInfo(
        message="incomplete chunked read",
        error_code="network_stream_interrupted",
        retryable=True,
        transport_error=True,
    )

    should_pause = AgentLlmSession._should_pause_for_recoverable_error(
        session,
        retry_error=retry_error,
        attempt_text_emitted=True,
        attempt_tool_event_emitted=False,
        attempt_messages_committed=False,
    )

    assert should_pause is True


def test_should_pause_for_recoverable_error_skips_safe_request_retry_case() -> None:
    session = object.__new__(AgentLlmSession)
    retry_error = LlmRetryErrorInfo(
        message="incomplete chunked read",
        error_code="network_stream_interrupted",
        retryable=True,
        transport_error=True,
    )

    should_pause = AgentLlmSession._should_pause_for_recoverable_error(
        session,
        retry_error=retry_error,
        attempt_text_emitted=False,
        attempt_tool_event_emitted=False,
        attempt_messages_committed=False,
    )

    assert should_pause is False


def test_normalize_committable_messages_keeps_request_fields() -> None:
    session = object.__new__(AgentLlmSession)
    request = ModelRequest(
        parts=[
            RetryPromptPart(
                content="validation failed",
                tool_name="shell",
                tool_call_id="call-1",
            )
        ],
        timestamp=datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC),
        instructions="System instructions",
        run_id="run-123",
        metadata={"source": "test"},
    )

    normalized = AgentLlmSession._normalize_committable_messages(session, [request])

    assert len(normalized) == 1
    normalized_request = normalized[0]
    assert isinstance(normalized_request, ModelRequest)
    assert normalized_request.instructions == "System instructions"
    assert normalized_request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert normalized_request.run_id == "run-123"
    assert normalized_request.metadata == {"source": "test"}
