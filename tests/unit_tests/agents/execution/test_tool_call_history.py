# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime

from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from relay_teams.agents.execution.tool_call_history import (
    normalize_replayed_messages,
    normalize_replayed_messages_against_pending,
)


def _request_with_metadata(*parts: ModelRequestPart) -> ModelRequest:
    return ModelRequest(
        parts=list(parts),
        timestamp=datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC),
        instructions="System instructions",
        run_id="run-123",
        metadata={"source": "test"},
    )


def test_normalize_replayed_messages_keeps_request_fields_after_dropping_orphan_tool_results() -> (
    None
):
    messages = [
        _request_with_metadata(UserPromptPart(content="continue")),
        _request_with_metadata(
            ToolReturnPart(
                tool_name="write",
                tool_call_id="call-missing",
                content={"ok": False},
            )
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="write",
                    args={"content": "hello"},
                    tool_call_id="call-real",
                )
            ]
        ),
    ]

    sanitized = normalize_replayed_messages(messages)

    assert len(sanitized) == 2
    request = sanitized[0]
    assert isinstance(request, ModelRequest)
    assert request.instructions == "System instructions"
    assert request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert request.run_id == "run-123"
    assert request.metadata == {"source": "test"}


def test_normalize_replayed_messages_keeps_request_fields_after_dropping_duplicate_tool_results() -> (
    None
):
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="write",
                    args={"content": "hello"},
                    tool_call_id="call-real",
                )
            ]
        ),
        _request_with_metadata(
            ToolReturnPart(
                tool_name="write",
                tool_call_id="call-real",
                content={"ok": True},
            )
        ),
        _request_with_metadata(
            ToolReturnPart(
                tool_name="write",
                tool_call_id="call-real",
                content={"ok": True},
            ),
            UserPromptPart(content="optimize it"),
        ),
    ]

    sanitized = normalize_replayed_messages(messages)

    assert len(sanitized) == 3
    request = sanitized[2]
    assert isinstance(request, ModelRequest)
    assert len(request.parts) == 1
    assert isinstance(request.parts[0], UserPromptPart)
    assert request.parts[0].content == "optimize it"
    assert request.instructions == "System instructions"
    assert request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert request.run_id == "run-123"
    assert request.metadata == {"source": "test"}


def test_normalize_replayed_messages_against_pending_keeps_request_fields() -> None:
    pending_tool_call_ids = {"call-real"}
    seen_tool_call_ids = {"call-real"}
    messages = [
        _request_with_metadata(
            ToolReturnPart(
                tool_name="write",
                tool_call_id="call-real",
                content={"ok": True},
            ),
            UserPromptPart(content="continue"),
        )
    ]

    sanitized = normalize_replayed_messages_against_pending(
        messages,
        pending_tool_call_ids=pending_tool_call_ids,
        seen_tool_call_ids=seen_tool_call_ids,
    )

    assert len(sanitized) == 1
    request = sanitized[0]
    assert isinstance(request, ModelRequest)
    assert request.instructions == "System instructions"
    assert request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert request.run_id == "run-123"
    assert request.metadata == {"source": "test"}


def test_normalize_replayed_messages_drops_tool_result_name_mismatch() -> None:
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="write",
                    args={"content": "hello"},
                    tool_call_id="call-real",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read",
                    tool_call_id="call-real",
                    content={"ok": True},
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="write",
                    tool_call_id="call-real",
                    content={"ok": True},
                )
            ]
        ),
    ]

    sanitized = normalize_replayed_messages(messages)

    assert len(sanitized) == 2
    assert isinstance(sanitized[0], ModelResponse)
    assert isinstance(sanitized[1], ModelRequest)
    assert len(sanitized[1].parts) == 1
    result_part = sanitized[1].parts[0]
    assert isinstance(result_part, ToolReturnPart)
    assert result_part.tool_name == "write"


def test_normalize_replayed_messages_keeps_retry_prompt_without_tool_name() -> None:
    messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="write",
                    args={"content": "hello"},
                    tool_call_id="call-real",
                )
            ]
        ),
        _request_with_metadata(
            RetryPromptPart(
                content="Input validation failed",
                tool_call_id="call-real",
            )
        ),
    ]

    sanitized = normalize_replayed_messages(messages)

    assert len(sanitized) == 2
    request = sanitized[1]
    assert isinstance(request, ModelRequest)
    assert len(request.parts) == 1
    retry_part = request.parts[0]
    assert isinstance(retry_part, RetryPromptPart)
    assert retry_part.tool_call_id == "call-real"
