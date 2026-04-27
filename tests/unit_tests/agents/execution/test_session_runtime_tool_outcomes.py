# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from pydantic import JsonValue
import pytest
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolReturnPart,
)

from relay_teams.agents.execution.event_publishing import EventPublishingService
from relay_teams.agents.execution.session_runtime import SessionRuntimeMixin
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent

from .agent_llm_session_test_support import _build_request


class _FakeRunEventHub:
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)


def _to_json_compatible(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        entries = cast(list[object], value)
        return [_to_json_compatible(entry) for entry in entries]
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        return {str(key): _to_json_compatible(entry) for key, entry in entries.items()}
    return str(value)


def _maybe_enrich_tool_result_payload(
    *,
    tool_name: str,
    result_payload: JsonValue,
) -> JsonValue:
    _ = tool_name
    return result_payload


def _tool_result_already_emitted_from_runtime(
    *,
    request: LLMRequest,
    tool_name: str,
    tool_call_id: str,
) -> bool:
    _ = (request, tool_name, tool_call_id)
    return False


@pytest.mark.asyncio
async def test_streamed_tool_result_is_published_for_commit_dedupe() -> None:
    session = object.__new__(SessionRuntimeMixin)
    request = _build_request()
    expected_request = request
    captured_messages: list[ModelRequest | ModelResponse] = []

    async def _fake_publish_tool_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        assert request == expected_request
        captured_messages.extend(messages)
        if published_tool_outcome_ids is not None:
            published_tool_outcome_ids.add("call-1")
        return True

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _fake_publish_tool_outcomes
    )
    published_tool_outcome_ids: set[str] = set()
    tool_result = ToolReturnPart(
        tool_name="test_tool",
        content={"ok": True},
        tool_call_id="call-1",
    )

    emitted = await SessionRuntimeMixin._publish_tool_outcome_event_from_stream_async(
        session,
        request=request,
        stream_event=FunctionToolResultEvent(result=tool_result),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted is True
    assert published_tool_outcome_ids == {"call-1"}
    assert len(captured_messages) == 1
    captured_message = captured_messages[0]
    assert isinstance(captured_message, ModelRequest)
    assert captured_message.parts == [tool_result]


@pytest.mark.asyncio
async def test_streamed_tool_validation_failure_is_published_as_tool_result() -> None:
    session = object.__new__(SessionRuntimeMixin)
    captured_messages: list[ModelRequest | ModelResponse] = []

    async def _fake_publish_tool_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = request
        captured_messages.extend(messages)
        if published_tool_outcome_ids is not None:
            published_tool_outcome_ids.add("call-invalid")
        return True

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _fake_publish_tool_outcomes
    )
    published_tool_outcome_ids: set[str] = set()

    emitted = await SessionRuntimeMixin._publish_tool_outcome_event_from_stream_async(
        session,
        request=_build_request(),
        stream_event=FunctionToolResultEvent(
            result=RetryPromptPart(
                content="invalid json",
                tool_name="read",
                tool_call_id="call-invalid",
            )
        ),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted is True
    assert published_tool_outcome_ids == {"call-invalid"}
    assert len(captured_messages) == 1
    captured_message = captured_messages[0]
    assert isinstance(captured_message, ModelRequest)
    captured_part = captured_message.parts[0]
    assert isinstance(captured_part, ToolReturnPart)
    assert captured_part.tool_name == "read"
    assert captured_part.tool_call_id == "call-invalid"
    assert isinstance(captured_part.content, dict)
    content = cast(dict[str, object], captured_part.content)
    assert content["ok"] is False
    error = content["error"]
    assert isinstance(error, dict)
    error_payload = cast(dict[str, object], error)
    assert error_payload["code"] == "tool_input_validation_failed"


def test_committed_tool_outcomes_skip_ids_published_from_stream() -> None:
    hub = _FakeRunEventHub()
    service = EventPublishingService(run_event_hub=hub)
    published_tool_outcome_ids: set[str] = set()
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="test_tool",
                    content={"ok": True},
                    tool_call_id="call-1",
                )
            ]
        )
    ]

    emitted_first = service.publish_committed_tool_outcome_events_from_messages(
        request=_build_request(),
        messages=messages,
        to_json_compatible=_to_json_compatible,
        maybe_enrich_tool_result_payload=_maybe_enrich_tool_result_payload,
        tool_result_already_emitted_from_runtime=(
            _tool_result_already_emitted_from_runtime
        ),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )
    emitted_second = service.publish_committed_tool_outcome_events_from_messages(
        request=_build_request(),
        messages=messages,
        to_json_compatible=_to_json_compatible,
        maybe_enrich_tool_result_payload=_maybe_enrich_tool_result_payload,
        tool_result_already_emitted_from_runtime=(
            _tool_result_already_emitted_from_runtime
        ),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted_first is True
    assert emitted_second is False
    assert published_tool_outcome_ids == {"call-1"}
    assert [event.event_type for event in hub.events] == [RunEventType.TOOL_RESULT]
    payload = json.loads(cast(str, hub.events[0].payload_json))
    assert payload["tool_call_id"] == "call-1"
    assert payload["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_non_tool_result_stream_event_is_ignored() -> None:
    session = object.__new__(SessionRuntimeMixin)

    async def _fake_publish_tool_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = (request, messages, published_tool_outcome_ids)
        raise AssertionError("unexpected tool outcome publish")

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _fake_publish_tool_outcomes
    )
    published_tool_outcome_ids: set[str] = set()

    emitted = await SessionRuntimeMixin._publish_tool_outcome_event_from_stream_async(
        session,
        request=_build_request(),
        stream_event=object(),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted is False
    assert published_tool_outcome_ids == set()


@pytest.mark.asyncio
async def test_function_tool_result_event_with_non_tool_result_is_ignored() -> None:
    session = object.__new__(SessionRuntimeMixin)

    async def _fake_publish_tool_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = (request, messages, published_tool_outcome_ids)
        raise AssertionError("unexpected tool outcome publish")

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _fake_publish_tool_outcomes
    )
    published_tool_outcome_ids: set[str] = set()
    unexpected_result = cast(ToolReturnPart | RetryPromptPart, object())

    emitted = await SessionRuntimeMixin._publish_tool_outcome_event_from_stream_async(
        session,
        request=_build_request(),
        stream_event=FunctionToolResultEvent(result=unexpected_result),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted is False
    assert published_tool_outcome_ids == set()


@pytest.mark.asyncio
async def test_streamed_retry_prompt_without_tool_name_is_ignored() -> None:
    session = object.__new__(SessionRuntimeMixin)

    async def _fake_publish_tool_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = (request, messages, published_tool_outcome_ids)
        raise AssertionError("unexpected tool outcome publish")

    session.__dict__["_publish_committed_tool_outcome_events_from_messages_async"] = (
        _fake_publish_tool_outcomes
    )
    published_tool_outcome_ids: set[str] = set()

    emitted = await SessionRuntimeMixin._publish_tool_outcome_event_from_stream_async(
        session,
        request=_build_request(),
        stream_event=FunctionToolResultEvent(
            result=RetryPromptPart(content="invalid", tool_call_id="call-missing")
        ),
        published_tool_outcome_ids=published_tool_outcome_ids,
    )

    assert emitted is False
    assert published_tool_outcome_ids == set()
