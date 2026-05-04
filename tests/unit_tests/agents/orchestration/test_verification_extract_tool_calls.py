# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import cast
from unittest.mock import MagicMock

from pydantic import JsonValue

from relay_teams.agents.orchestration.verification import (
    _extract_tool_call_events,
    _parse_event_payload,
)
from relay_teams.sessions.runs.event_log import EventLog


def _make_event_bus(
    events: tuple[dict[str, JsonValue], ...],
) -> EventLog:
    bus = MagicMock(spec=EventLog)
    bus.list_by_trace.return_value = events
    return cast(EventLog, bus)


class TestExtractToolCallEvents:
    def test_extracts_matching_tool_calls(self) -> None:
        events = (
            {
                "task_id": cast(JsonValue, "t1"),
                "payload_json": cast(
                    JsonValue,
                    json.dumps({"tool_name": "read", "tool_args": {"path": "x"}}),
                ),
            },
            {
                "task_id": cast(JsonValue, "t2"),
                "payload_json": cast(JsonValue, json.dumps({"tool_name": "write"})),
            },
            {
                "task_id": cast(JsonValue, "t1"),
                "payload_json": cast(JsonValue, json.dumps({"not_a_tool": True})),
            },
        )
        bus = _make_event_bus(events)
        calls = _extract_tool_call_events(
            event_bus=bus, trace_id="trace-1", task_id="t1"
        )
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "read"

    def test_skipping_non_matching_task_id(self) -> None:
        events = (
            {
                "task_id": cast(JsonValue, "other"),
                "payload_json": cast(JsonValue, json.dumps({"tool_name": "run"})),
            },
        )
        bus = _make_event_bus(events)
        calls = _extract_tool_call_events(
            event_bus=bus, trace_id="trace-1", task_id="t1"
        )
        assert len(calls) == 0

    def test_empty_events(self) -> None:
        bus = _make_event_bus(())
        calls = _extract_tool_call_events(
            event_bus=bus, trace_id="trace-1", task_id="t1"
        )
        assert calls == ()


class TestParseEventPayload:
    def test_non_string_returns_empty(self) -> None:
        assert _parse_event_payload(42) == {}

    def test_empty_string_returns_empty(self) -> None:
        assert _parse_event_payload("") == {}

    def test_invalid_json_returns_empty(self) -> None:
        assert _parse_event_payload("not json") == {}

    def test_non_dict_json_returns_empty(self) -> None:
        assert _parse_event_payload("[1,2,3]") == {}

    def test_valid_dict_json(self) -> None:
        result = _parse_event_payload('{"key": "val"}')
        assert result == {"key": "val"}
