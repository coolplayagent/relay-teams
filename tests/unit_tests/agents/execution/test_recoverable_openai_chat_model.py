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


def test_map_tool_call_repairs_invalid_json_arguments_for_replay() -> None:
    tool_call = ToolCallPart(
        tool_name="write",
        args='{"content":"hello", path:"demo.txt"}',
        tool_call_id="call-invalid",
    )

    mapped = RecoverableOpenAIChatModel._map_tool_call(tool_call)

    parsed = json.loads(mapped["function"]["arguments"])
    assert parsed == {"content": "hello", "path": "demo.txt"}


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
