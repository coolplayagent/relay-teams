# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from agent_teams.agents.execution.recoverable_openai_chat_model import (
    RecoverableOpenAIChatModel,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def test_map_tool_call_keeps_valid_json_arguments() -> None:
    tool_call = ToolCallPart(
        tool_name="write",
        args='{"content":"hello","path":"demo.txt"}',
        tool_call_id="call-valid",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    assert mapped["function"]["arguments"] == '{"content":"hello","path":"demo.txt"}'


def test_map_tool_call_keeps_valid_json_arguments_with_null_fields() -> None:
    tool_call = ToolCallPart(
        tool_name="shell",
        args='{"command":"pwd","background":true,"yield_time_ms":null}',
        tool_call_id="call-valid-null",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    assert mapped["function"]["arguments"] == (
        '{"command":"pwd","background":true,"yield_time_ms":null}'
    )


def test_map_tool_call_repairs_invalid_json_arguments_for_replay() -> None:
    tool_call = ToolCallPart(
        tool_name="write",
        args='{"content":"hello", path:"demo.txt"}',
        tool_call_id="call-invalid",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    parsed = json.loads(mapped["function"]["arguments"])
    assert parsed == {"content": "hello", "path": "demo.txt"}


def test_map_tool_call_repairs_invalid_string_escape_arguments_for_replay() -> None:
    tool_call = ToolCallPart(
        tool_name="shell",
        args=('{"command":"python -c \\"print(\\\'hello\\\')\\"","background":true}'),
        tool_call_id="call-invalid-escape",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    parsed = json.loads(mapped["function"]["arguments"])
    assert parsed == {
        "command": "python -c \"print('hello')\"",
        "background": True,
    }


def test_map_tool_call_wraps_non_object_json_arguments_for_replay() -> None:
    tool_call = ToolCallPart(
        tool_name="write",
        args='["not","an","object"]',
        tool_call_id="call-array",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    parsed = json.loads(mapped["function"]["arguments"])
    assert parsed == {"INVALID_JSON": '["not","an","object"]'}


def test_map_tool_call_wraps_unrepairable_invalid_json_arguments_for_replay() -> None:
    tool_call = ToolCallPart(
        tool_name="write",
        args="not-json-at-all",
        tool_call_id="call-text",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    parsed = json.loads(mapped["function"]["arguments"])
    assert parsed == {"INVALID_JSON": "not-json-at-all"}


def test_sanitize_replayed_messages_drops_orphan_tool_results() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content="continue")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="write",
                    tool_call_id="call-missing",
                    content={"ok": False},
                )
            ]
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

    sanitized = RecoverableOpenAIChatModel._sanitize_replayed_messages(messages)

    assert len(sanitized) == 2
    assert isinstance(sanitized[0], ModelRequest)
    assert isinstance(sanitized[1], ModelResponse)


def test_sanitize_replayed_messages_drops_duplicate_late_tool_results() -> None:
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
                    tool_name="write",
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
                ),
                UserPromptPart(content="optimize it"),
            ]
        ),
    ]

    sanitized = RecoverableOpenAIChatModel._sanitize_replayed_messages(messages)

    assert len(sanitized) == 3
    assert isinstance(sanitized[2], ModelRequest)
    assert len(sanitized[2].parts) == 1
    assert isinstance(sanitized[2].parts[0], UserPromptPart)
    assert sanitized[2].parts[0].content == "optimize it"
