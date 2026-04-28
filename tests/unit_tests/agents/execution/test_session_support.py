# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import ThinkingPart

from relay_teams.agents.execution.tool_result_state import ToolResultStateService
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.persistence.scope_models import ScopeRef, ScopeType, StateMutation
from relay_teams.persistence.shared_state_repo import SharedStateRepository
from relay_teams.agents.execution import session_support as session_support_module
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallBatchState,
    PersistedToolCallBatchItem,
    ToolCallBatchStatus,
    load_tool_call_state,
    merge_tool_call_batch_state,
    merge_tool_call_state,
)

from .agent_llm_session_test_support import (
    AgentLlmSession,
    JsonValue,
    LLMRequest,
    MessageRepository,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    PersistedToolCallState,
    RunEvent,
    RunEventType,
    TextPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
    ToolExecutionStatus,
    ToolReturnPart,
    _FakeMessageRepo,
    _build_request,
)


class _FakeEventLog:
    def __init__(self, events: tuple[dict[str, JsonValue], ...]) -> None:
        self._events = events
        self.requested_trace_ids: list[str] = []

    def list_by_trace(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        self.requested_trace_ids.append(trace_id)
        return self._events

    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        self.requested_trace_ids.append(trace_id)
        return self._events


def test_apply_streamed_text_fallback_repairs_truncated_final_message() -> None:
    session = object.__new__(AgentLlmSession)
    messages: list[ModelRequest | ModelResponse] = [
        ModelResponse(parts=[TextPart(content="lunar-min")], model_name="fake-model")
    ]

    repaired = AgentLlmSession._apply_streamed_text_fallback(
        session,
        messages,
        streamed_text="lunar-mint-407",
    )

    assert len(repaired) == 1
    repaired_response = repaired[0]
    assert isinstance(repaired_response, ModelResponse)
    assert AgentLlmSession._extract_text(session, repaired_response) == "lunar-mint-407"


def test_apply_streamed_text_fallback_replaces_only_one_text_segment() -> None:
    session = object.__new__(AgentLlmSession)
    messages: list[ModelRequest | ModelResponse] = [
        ModelResponse(
            parts=[
                TextPart(content="lunar-"),
                TextPart(content="mint"),
            ],
            model_name="fake-model",
        )
    ]

    repaired = AgentLlmSession._apply_streamed_text_fallback(
        session,
        messages,
        streamed_text="lunar-mint-407",
    )

    repaired_response = repaired[0]
    assert isinstance(repaired_response, ModelResponse)
    text_parts = [
        part for part in repaired_response.parts if isinstance(part, TextPart)
    ]
    assert len(text_parts) == 1
    assert AgentLlmSession._extract_text(session, repaired_response) == "lunar-mint-407"


def test_apply_streamed_text_fallback_skips_tool_call_responses() -> None:
    session = object.__new__(AgentLlmSession)
    original_response = ModelResponse(
        parts=[
            TextPart(content="partial"),
            ToolCallPart(
                tool_name="search", args='{"q":"moon"}', tool_call_id="call-1"
            ),
        ],
        model_name="fake-model",
    )
    messages: list[ModelRequest | ModelResponse] = [original_response]

    repaired = AgentLlmSession._apply_streamed_text_fallback(
        session,
        messages,
        streamed_text="should-not-overwrite",
    )

    assert repaired == messages
    assert repaired[0] is original_response


def test_publish_text_and_thinking_events_emit_expected_run_events() -> None:
    session = object.__new__(AgentLlmSession)
    published_events: list[RunEvent] = []
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub",
        (),
        {"publish": lambda self, event: published_events.append(event)},
    )()

    AgentLlmSession._publish_text_delta_event(
        session,
        request=_build_request(),
        text="hello",
    )
    AgentLlmSession._publish_thinking_started_event(
        session,
        request=_build_request(),
        part_index=3,
    )
    AgentLlmSession._publish_thinking_delta_event(
        session,
        request=_build_request(),
        part_index=3,
        text="plan",
    )
    AgentLlmSession._publish_thinking_finished_event(
        session,
        request=_build_request(),
        part_index=3,
    )

    assert len(published_events) == 4
    assert published_events[0].event_type == RunEventType.TEXT_DELTA
    assert json.loads(cast(str, published_events[0].payload_json)) == {
        "text": "hello",
        "role_id": "writer",
        "instance_id": "inst-1",
    }
    assert published_events[1].event_type == RunEventType.THINKING_STARTED
    assert json.loads(cast(str, published_events[1].payload_json)) == {
        "part_index": 3,
        "role_id": "writer",
        "instance_id": "inst-1",
    }
    assert published_events[2].event_type == RunEventType.THINKING_DELTA
    assert json.loads(cast(str, published_events[2].payload_json)) == {
        "part_index": 3,
        "text": "plan",
        "role_id": "writer",
        "instance_id": "inst-1",
    }
    assert published_events[3].event_type == RunEventType.THINKING_FINISHED
    assert json.loads(cast(str, published_events[3].payload_json)) == {
        "part_index": 3,
        "role_id": "writer",
        "instance_id": "inst-1",
    }


def test_visible_tool_result_from_state_returns_sanitized_visible_result() -> None:
    session = object.__new__(AgentLlmSession)
    result_envelope: dict[str, JsonValue] = {
        "visible_result": {
            "ok": True,
            "data": {
                "task": {"status": "completed", "started_at": "2026-04-22T12:00:00Z"}
            },
        },
        "runtime_meta": {"tool_result_event_published": True},
    }
    state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope=result_envelope,
    )

    visible = AgentLlmSession._visible_tool_result_from_state(
        session,
        state=state,
        expected_tool_name="search",
    )

    assert visible == {
        "ok": True,
        "data": {
            "task": {
                "status": "completed",
                "started_at": "2026-04-22T12:00:00Z",
            }
        },
    }


def test_visible_tool_result_from_state_returns_none_without_published_marker() -> None:
    session = object.__new__(AgentLlmSession)
    state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "visible_result": {
                "ok": True,
                "data": {"task": {"status": "completed"}},
            }
        },
    )

    visible = AgentLlmSession._visible_tool_result_from_state(
        session,
        state=state,
        expected_tool_name="search",
    )

    assert visible is None


def test_visible_tool_result_from_state_returns_durable_pre_publish_result() -> None:
    session = object.__new__(AgentLlmSession)
    state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "visible_result": {
                "ok": True,
                "data": {"task": {"status": "completed"}},
            },
            "runtime_meta": {
                "tool_result_durably_recorded": True,
                "tool_result_event_published": False,
            },
        },
    )

    visible = AgentLlmSession._visible_tool_result_from_state(
        session,
        state=state,
        expected_tool_name="search",
    )

    assert visible == {"ok": True, "data": {"task": {"status": "completed"}}}


def test_tool_result_state_checks_runtime_event_id_paths() -> None:
    request = _build_request()
    state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={"ok": True, "meta": {"tool_result_event_published": False}},
        result_event_id=42,
    )
    service = ToolResultStateService()

    assert (
        service.tool_result_was_durably_recorded(
            result_envelope={"ok": True, "meta": "not-meta"}
        )
        is False
    )
    assert service.tool_result_already_emitted_from_runtime(
        request=request,
        tool_name="search",
        tool_call_id="call-1",
        shared_store=object(),
        load_tool_call_state=lambda **_kwargs: state,
    )


@pytest.mark.asyncio
async def test_tool_result_state_checks_async_runtime_event_id_path() -> None:
    request = _build_request()
    state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={"ok": True, "meta": {"tool_result_event_published": False}},
        result_event_id=42,
    )

    async def load_state(**_kwargs: object) -> PersistedToolCallState:
        return state

    assert (
        await ToolResultStateService().tool_result_already_emitted_from_runtime_async(
            request=request,
            tool_name="search",
            tool_call_id="call-1",
            shared_store=object(),
            load_tool_call_state=load_state,
        )
    )


def test_filter_model_messages_drops_thinking_only_response() -> None:
    session = object.__new__(AgentLlmSession)
    filtered = AgentLlmSession._filter_model_messages(
        session,
        [
            ModelResponse(parts=[ThinkingPart(content="plan")]),
            ModelResponse(parts=[TextPart(content="visible")]),
        ],
    )

    assert len(filtered) == 1
    assert isinstance(filtered[0], ModelResponse)


def test_tool_args_from_preview_handles_empty_invalid_and_scalar_json() -> None:
    assert session_support_module._tool_args_from_preview("") == {}
    assert session_support_module._tool_args_from_preview("{not-json") == "{not-json"
    assert session_support_module._tool_args_from_preview('"scalar"') == '"scalar"'


def test_handle_part_start_event_emits_text_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    emitted_chunks: list[str] = []
    published_text: list[str] = []
    logged_chunks: list[tuple[str, str]] = []
    session.__dict__["_publish_text_delta_event"] = lambda **kwargs: (
        published_text.append(cast(str, kwargs["text"]))
    )
    monkeypatch.setattr(
        session_support_module,
        "log_model_stream_chunk",
        lambda role_id, text: logged_chunks.append((role_id, text)),
    )

    emitted = AgentLlmSession._handle_part_start_event(
        session,
        request=_build_request(),
        event=PartStartEvent(index=0, part=TextPart(content="hello")),
        emitted_text_chunks=emitted_chunks,
        text_lengths={},
        thinking_lengths={},
        started_thinking_parts=set(),
        streamed_tool_calls={},
    )

    assert emitted is True
    assert emitted_chunks == ["hello"]
    assert published_text == ["hello"]
    assert logged_chunks == [("writer", "hello")]


def test_handle_part_delta_event_emits_thinking_started_once() -> None:
    session = object.__new__(AgentLlmSession)
    started_parts: list[int] = []
    delta_events: list[tuple[int, str]] = []
    session.__dict__["_publish_thinking_started_event"] = lambda **kwargs: (
        started_parts.append(cast(int, kwargs["part_index"]))
    )
    session.__dict__["_publish_thinking_delta_event"] = lambda **kwargs: (
        delta_events.append(
            (cast(int, kwargs["part_index"]), cast(str, kwargs["text"]))
        )
    )
    tracked_started_parts: set[int] = set()
    thinking_lengths: dict[int, int] = {}

    first_emitted = AgentLlmSession._handle_part_delta_event(
        session,
        request=_build_request(),
        event=PartDeltaEvent(index=1, delta=ThinkingPartDelta(content_delta="plan")),
        emitted_text_chunks=[],
        text_lengths={},
        thinking_lengths=thinking_lengths,
        started_thinking_parts=tracked_started_parts,
        streamed_tool_calls={},
    )
    second_emitted = AgentLlmSession._handle_part_delta_event(
        session,
        request=_build_request(),
        event=PartDeltaEvent(index=1, delta=ThinkingPartDelta(content_delta=" more")),
        emitted_text_chunks=[],
        text_lengths={},
        thinking_lengths=thinking_lengths,
        started_thinking_parts=tracked_started_parts,
        streamed_tool_calls={},
    )

    assert first_emitted is False
    assert second_emitted is False
    assert started_parts == [1]
    assert delta_events == [(1, "plan"), (1, " more")]
    assert thinking_lengths == {1: 9}
    assert tracked_started_parts == {1}


def test_handle_part_delta_event_materializes_tool_call_delta() -> None:
    session = object.__new__(AgentLlmSession)
    streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta] = {}

    emitted = AgentLlmSession._handle_part_delta_event(
        session,
        request=_build_request(),
        event=PartDeltaEvent(
            index=2,
            delta=ToolCallPartDelta(
                tool_name_delta="search",
                args_delta='{"q":"moon"}',
                tool_call_id="call-1",
            ),
        ),
        emitted_text_chunks=[],
        text_lengths={},
        thinking_lengths={},
        started_thinking_parts=set(),
        streamed_tool_calls=streamed_tool_calls,
    )

    assert emitted is False
    materialized = streamed_tool_calls[2]
    assert isinstance(materialized, ToolCallPart)
    assert materialized.tool_name == "search"
    assert materialized.args == '{"q":"moon"}'
    assert materialized.tool_call_id == "call-1"


def test_collect_salvageable_stream_tool_calls_repairs_invalid_json_args() -> None:
    session = object.__new__(AgentLlmSession)

    salvaged = AgentLlmSession._collect_salvageable_stream_tool_calls(
        session,
        {
            0: ToolCallPart(
                tool_name="search",
                args='{"q":"moon"',
                tool_call_id="call-1",
            )
        },
    )

    assert len(salvaged) == 1
    recovered = salvaged[0]
    assert recovered.tool_name == "search"
    assert recovered.tool_call_id == "call-1"
    assert recovered.args == {"q": "moon"}


@pytest.mark.asyncio
async def test_maybe_recover_from_tool_args_parse_failure_returns_none_without_parse_error() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    message_repo = _FakeMessageRepo(history=[])
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)

    result = await AgentLlmSession._maybe_recover_from_tool_args_parse_failure(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=2,
        emitted_text_chunks=["partial"],
        published_tool_call_ids=set(),
        streamed_tool_calls={
            0: ToolCallPart(
                tool_name="search",
                args='{"q":"moon"',
                tool_call_id="call-1",
            )
        },
        error_message="provider disconnected unexpectedly",
    )

    assert result is None
    assert message_repo.pruned_conversation_ids == []
    assert message_repo.append_calls == []


@pytest.mark.asyncio
async def test_maybe_recover_from_tool_args_parse_failure_persists_recovery_and_retries() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    message_repo = _FakeMessageRepo(history=[])
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    published_tool_call_messages: list[list[object]] = []
    committed_tool_outcome_messages: list[list[object]] = []
    generate_calls: list[dict[str, object]] = []
    session.__dict__["_publish_tool_call_events_from_messages"] = lambda **kwargs: (
        published_tool_call_messages.append(cast(list[object], kwargs["messages"]))
    )
    session.__dict__["_publish_committed_tool_outcome_events_from_messages"] = (
        lambda **kwargs: committed_tool_outcome_messages.append(
            cast(list[object], kwargs["messages"])
        )
    )

    async def _generate_async(
        request: LLMRequest,
        **kwargs: object,
    ) -> str:
        generate_calls.append({"request": request, **kwargs})
        return "resumed"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._maybe_recover_from_tool_args_parse_failure(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=3,
        emitted_text_chunks=["partial answer"],
        published_tool_call_ids=set(),
        streamed_tool_calls={
            0: ToolCallPart(
                tool_name="search",
                args='{"q":"moon"',
                tool_call_id="call-1",
            )
        },
        error_message="Expecting ',' delimiter: line 1 column 12 (char 11)",
    )

    assert result == "resumed"
    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    persisted_messages = message_repo.append_calls[0]
    assert len(persisted_messages) == 2
    assistant_response = persisted_messages[0]
    tool_error_request = persisted_messages[1]
    assert isinstance(assistant_response, ModelResponse)
    assert isinstance(tool_error_request, ModelRequest)
    assert len(assistant_response.parts) == 2
    assert isinstance(assistant_response.parts[0], TextPart)
    assert assistant_response.parts[0].content == "partial answer"
    assert isinstance(assistant_response.parts[1], ToolCallPart)
    assert assistant_response.parts[1].args == {"q": "moon"}
    assert len(tool_error_request.parts) == 1
    tool_error_part = tool_error_request.parts[0]
    assert isinstance(tool_error_part, ToolReturnPart)
    assert tool_error_part.tool_name == "search"
    assert tool_error_part.tool_call_id == "call-1"
    assert published_tool_call_messages == [[assistant_response]]
    assert committed_tool_outcome_messages == [[tool_error_request]]
    assert generate_calls == [
        {
            "request": _build_request(),
            "retry_number": 1,
            "total_attempts": 3,
            "skip_initial_user_prompt_persist": True,
        }
    ]


@pytest.mark.asyncio
async def test_maybe_recover_from_tool_args_parse_failure_raises_terminal_error_when_budget_exhausted() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    message_repo = _FakeMessageRepo(history=[])
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    raised_errors: list[dict[str, object]] = []
    session.__dict__["_publish_tool_call_events_from_messages"] = lambda **kwargs: None
    session.__dict__["_publish_committed_tool_outcome_events_from_messages"] = (
        lambda **kwargs: None
    )
    session.__dict__["_generate_async"] = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("recovery exhaustion should not retry")
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        raised_errors.append(kwargs),
        (_ for _ in ()).throw(RuntimeError("terminal")),
    )

    with pytest.raises(RuntimeError, match="terminal"):
        await AgentLlmSession._maybe_recover_from_tool_args_parse_failure(
            session,
            request=_build_request(),
            retry_number=0,
            total_attempts=1,
            emitted_text_chunks=["partial answer"],
            published_tool_call_ids=set(),
            streamed_tool_calls={
                0: ToolCallPart(
                    tool_name="search",
                    args='{"q":"moon"',
                    tool_call_id="call-1",
                )
            },
            error_message="Expecting ',' delimiter: line 1 column 12 (char 11)",
        )

    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    assert len(raised_errors) == 1
    assert raised_errors[0]["error_code"] == "model_tool_args_invalid_json"
    assert "Expecting ',' delimiter" in cast(str, raised_errors[0]["error_message"])


@pytest.mark.asyncio
async def test_restore_pending_tool_results_from_state_backfills_completed_dispatch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    persisted_state = PersistedToolCallState(
        tool_call_id="call-dispatch-1",
        tool_name="orch_dispatch_task",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "tool": "orch_dispatch_task",
            "visible_result": {
                "ok": True,
                "data": {
                    "task": {
                        "task_id": "task-child-1",
                        "status": "completed",
                        "result": "Shanghai weather collected.",
                    }
                },
                "meta": {"tool_result_event_published": True},
            },
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    monkeypatch.setattr(
        session_support_module,
        "load_or_recover_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="orch_dispatch_task",
                        args='{"task_id":"task-child-1","role_id":"Crafter"}',
                        tool_call_id="call-dispatch-1",
                    )
                ]
            )
        ],
    )

    assert recovered_count == 1
    assert len(recovered_messages) == 2
    synthetic_request = recovered_messages[-1]
    assert isinstance(synthetic_request, ModelRequest)
    assert len(synthetic_request.parts) == 1
    recovered_part = synthetic_request.parts[0]
    assert isinstance(recovered_part, ToolReturnPart)
    assert recovered_part.tool_name == "orch_dispatch_task"
    assert recovered_part.tool_call_id == "call-dispatch-1"
    assert recovered_part.content == {
        "ok": True,
        "data": {
            "task": {
                "task_id": "task-child-1",
                "status": "completed",
                "result": "Shanghai weather collected.",
            }
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_from_state_reattaches_orphaned_spawn_subagent_call(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-1",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-1",
            "subagent_instance_id": "subagent-inst-1",
            "subagent_task_id": "task-sub-1",
            "subagent_role_id": "Explorer",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "title": "Investigate",
            "prompt": "Inspect the failing tests and summarize the root cause.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-1",
            value_json=state.model_dump_json(),
        )
    )
    wait_for_subagent_run_calls: list[tuple[str, str]] = []

    class _FakeBackgroundTaskService:
        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            wait_for_subagent_run_calls.append((parent_run_id, subagent_run_id))
            return type(
                "_Result",
                (),
                {
                    "run_id": "subagent-run-1",
                    "output": "root cause found",
                },
            )()

        async def run_subagent(self, **kwargs: object) -> object:
            _ = kwargs
            raise AssertionError("orphan recovery must not start a new subagent run")

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert wait_for_subagent_run_calls == [("run-1", "subagent-run-1")]
    assert len(recovered_messages) == 2
    recovered_call = recovered_messages[0]
    assert isinstance(recovered_call, ModelResponse)
    assert isinstance(recovered_call.parts[0], ToolCallPart)
    assert recovered_call.parts[0].tool_name == "spawn_subagent"
    assert recovered_call.parts[0].tool_call_id == "call-subagent-1"
    assert recovered_call.parts[0].args == {
        "role_id": "Explorer",
        "description": "Investigate",
        "prompt": "Inspect the failing tests and summarize the root cause.",
        "background": False,
    }
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    assert isinstance(recovered_result.parts[0], ToolReturnPart)
    assert recovered_result.parts[0].tool_name == "spawn_subagent"
    assert recovered_result.parts[0].tool_call_id == "call-subagent-1"
    assert recovered_result.parts[0].content == {
        "ok": True,
        "data": {
            "completed": True,
            "output": "root cause found",
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_from_state_reattaches_ready_spawn_subagent_call(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-ready",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.READY,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-ready",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Inspect the failing tests and summarize the root cause.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-ready",
            value_json=state.model_dump_json(),
        )
    )
    wait_for_subagent_run_calls: list[tuple[str, str]] = []

    class _FakeBackgroundTaskService:
        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            wait_for_subagent_run_calls.append((parent_run_id, subagent_run_id))
            return type(
                "_Result",
                (),
                {
                    "run_id": "subagent-run-ready",
                    "output": "ready result",
                },
            )()

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert wait_for_subagent_run_calls == [("run-1", "subagent-run-ready")]
    assert len(recovered_messages) == 2
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    assert isinstance(recovered_result.parts[0], ToolReturnPart)
    assert recovered_result.parts[0].content == {
        "ok": True,
        "data": {
            "completed": True,
            "output": "ready result",
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_reattaches_subagent_from_record_lookup(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-record",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Inspect the failing tests and summarize the root cause.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-record",
            value_json=state.model_dump_json(),
        )
    )
    record = BackgroundTaskRecord(
        background_task_id="sync-subagent-record",
        run_id="run-1",
        session_id="session-1",
        kind=BackgroundTaskKind.SUBAGENT,
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-subagent-record",
        title="Investigate",
        input_text="Inspect the failing tests and summarize the root cause.",
        command="subagent:Explorer",
        cwd="workspace-1",
        execution_mode="foreground",
        status=BackgroundTaskStatus.RUNNING,
        tty=False,
        subagent_role_id="Explorer",
        subagent_run_id="subagent-run-record",
        subagent_task_id="task-sub-record",
        subagent_instance_id="inst-sub-record",
    )
    wait_for_subagent_run_calls: list[tuple[str, str]] = []

    class _FakeBackgroundTaskService:
        def subagent_record_for_tool_call(
            self,
            *,
            parent_run_id: str,
            tool_call_id: str,
        ) -> BackgroundTaskRecord | None:
            assert parent_run_id == "run-1"
            assert tool_call_id == "call-subagent-record"
            return record

        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            wait_for_subagent_run_calls.append((parent_run_id, subagent_run_id))
            return type(
                "_Result",
                (),
                {
                    "run_id": "subagent-run-record",
                    "output": "record result",
                },
            )()

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert wait_for_subagent_run_calls == [("run-1", "subagent-run-record")]
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    result_part = recovered_result.parts[0]
    assert isinstance(result_part, ToolReturnPart)
    assert result_part.content == {
        "ok": True,
        "data": {
            "completed": True,
            "output": "record result",
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_recovers_background_subagent_result(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-background",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_background",
            "background": True,
            "requested_role_id": "Explorer",
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-background",
            value_json=state.model_dump_json(),
        )
    )
    record = BackgroundTaskRecord(
        background_task_id="background-subagent-record",
        run_id="run-1",
        session_id="session-1",
        kind=BackgroundTaskKind.SUBAGENT,
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-subagent-background",
        title="Investigate",
        input_text="Inspect in background.",
        command="subagent:Explorer",
        cwd="workspace-1",
        execution_mode="background",
        status=BackgroundTaskStatus.RUNNING,
        tty=False,
        subagent_role_id="Explorer",
        subagent_run_id="subagent-run-background",
        subagent_task_id="task-sub-background",
        subagent_instance_id="inst-sub-background",
    )

    class _FakeBackgroundTaskService:
        def subagent_record_for_tool_call(
            self,
            *,
            parent_run_id: str,
            tool_call_id: str,
        ) -> BackgroundTaskRecord | None:
            assert parent_run_id == "run-1"
            assert tool_call_id == "call-subagent-background"
            return record

        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            raise AssertionError((parent_run_id, subagent_run_id))

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()
    pending_messages = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="spawn_subagent",
                    args={
                        "role_id": "Explorer",
                        "description": "Investigate",
                        "prompt": "Inspect in background.",
                        "background": True,
                    },
                    tool_call_id="call-subagent-background",
                )
            ]
        )
    ]

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=pending_messages,
    )

    assert recovered_count == 1
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    result_part = recovered_result.parts[0]
    assert isinstance(result_part, ToolReturnPart)
    content = result_part.content
    assert isinstance(content, dict)
    assert content["ok"] is True
    data = content["data"]
    assert isinstance(data, dict)
    assert data["background_task_id"] == "background-subagent-record"
    assert data["completed"] is False
    assert data["subagent_run_id"] == "subagent-run-background"


@pytest.mark.asyncio
async def test_running_spawn_subagent_without_durable_launch_reinvokes_batch_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    state = PersistedToolCallState(
        tool_call_id="call-subagent-prelaunch",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "requested_role_id": "Explorer",
            "background": False,
        },
    )
    invoke_calls: list[dict[str, object]] = []

    class _FakeBackgroundTaskService:
        def subagent_record_for_tool_call(
            self,
            *,
            parent_run_id: str,
            tool_call_id: str,
        ) -> BackgroundTaskRecord | None:
            assert parent_run_id == "run-1"
            assert tool_call_id == "call-subagent-prelaunch"
            return None

        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            raise AssertionError((parent_run_id, subagent_run_id))

    class _FakeRecoverableToolInvoker:
        async def invoke_async(
            self,
            *,
            tool_registry: object,
            deps: object,
            tool_name: str,
            tool_call_id: str,
            raw_args: object,
        ) -> dict[str, JsonValue]:
            invoke_calls.append(
                {
                    "tool_registry": tool_registry,
                    "deps": deps,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "raw_args": raw_args,
                }
            )
            return {"ok": True, "data": {"reinvoked": True}}

    monkeypatch.setattr(
        session_support_module,
        "RecoverableToolInvoker",
        _FakeRecoverableToolInvoker,
    )
    session.__dict__["_tool_registry"] = object()
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    visible = await AgentLlmSession._visible_result_for_batch_item(
        session,
        request=_build_request(),
        deps=cast(ToolDeps, object()),
        state=state,
        tool_call_id="call-subagent-prelaunch",
        tool_name="spawn_subagent",
        raw_args={
            "role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Recover this subagent launch.",
            "background": False,
        },
        recover_ready_calls=True,
    )

    assert visible == {"ok": True, "data": {"reinvoked": True}}
    assert len(invoke_calls) == 1
    assert invoke_calls[0]["tool_name"] == "spawn_subagent"
    assert invoke_calls[0]["tool_call_id"] == "call-subagent-prelaunch"


def test_task_tool_call_batch_states_handles_missing_store_and_invalid_rows(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    setattr(session, "_shared_store", None)
    assert AgentLlmSession._task_tool_call_batch_states(session, "task-1") == ()

    shared_store = SharedStateRepository(tmp_path / "session-batch-states.db")
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_batch:bad",
            value_json="{not-json",
        )
    )
    good = PersistedToolCallBatchState(
        batch_id="batch-1",
        instance_id="inst-1",
        role_id="writer",
        task_id="task-1",
        status=ToolCallBatchStatus.SEALED,
        items=(
            PersistedToolCallBatchItem(
                tool_call_id="call-1",
                tool_name="search",
                index=0,
            ),
        ),
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_batch:good",
            value_json=good.model_dump_json(),
        )
    )
    session._shared_store = shared_store

    assert AgentLlmSession._task_tool_call_batch_states(session, "task-1") == (good,)


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_ignores_event_log_read_failures(
    tmp_path: Path,
) -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    history: list[ModelRequest | ModelResponse] = []

    class _FailingBatchReplayEventLog:
        def list_by_trace_with_ids(
            self,
            trace_id: str,
        ) -> tuple[dict[str, JsonValue], ...]:
            _ = trace_id
            raise sqlite3.OperationalError("database is locked")

    session.__dict__["_shared_store"] = SharedStateRepository(
        tmp_path / "session-batch-read-failure.db"
    )
    session.__dict__["_event_bus"] = _FailingBatchReplayEventLog()
    session.__dict__["_message_repo"] = None

    (
        recovered_history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=history,
        deps=cast(ToolDeps, object()),
        recover_ready_calls=True,
    )

    assert recovered_history == history
    assert recovered_count == 0


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_ignores_batch_snapshot_failures() -> (
    None
):
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    history: list[ModelRequest | ModelResponse] = []

    class _FailingBatchSnapshotStore:
        def snapshot(self, scope: ScopeRef) -> tuple[tuple[str, str], ...]:
            _ = scope
            raise sqlite3.OperationalError("database is locked")

    session.__dict__["_shared_store"] = _FailingBatchSnapshotStore()
    session.__dict__["_event_bus"] = _FakeEventLog(())
    session.__dict__["_message_repo"] = None

    (
        recovered_history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=history,
        deps=cast(ToolDeps, object()),
        recover_ready_calls=True,
    )

    assert recovered_history == history
    assert recovered_count == 0


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_recovers_open_observed_batch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-open-batch-recovery.db")
    merge_tool_call_batch_state(
        shared_store=shared_store,
        task_id=request.task_id,
        batch_id="batch-open",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        status=ToolCallBatchStatus.OPEN,
        items=(
            PersistedToolCallBatchItem(
                tool_call_id="call-open",
                tool_name="read_file",
                args_preview='{"path":"README.md"}',
                index=0,
            ),
        ),
    )
    merge_tool_call_state(
        shared_store=shared_store,
        task_id=request.task_id,
        tool_call_id="call-open",
        tool_name="read_file",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        args_preview='{"path":"README.md"}',
        execution_status=ToolExecutionStatus.READY,
        batch_id="batch-open",
        batch_index=0,
        batch_size=0,
    )
    invoke_calls: list[dict[str, object]] = []

    class _FakeRecoverableToolInvoker:
        async def invoke_async(
            self,
            *,
            tool_registry: object,
            deps: object,
            tool_name: str,
            tool_call_id: str,
            raw_args: object,
        ) -> dict[str, JsonValue]:
            invoke_calls.append(
                {
                    "tool_registry": tool_registry,
                    "deps": deps,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "raw_args": raw_args,
                }
            )
            return {"ok": True, "data": {"content": "readme"}}

    committed_messages: list[ModelRequest | ModelResponse] = []

    async def _commit_all_safe_messages_async(
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        list[int],
        list[str],
    ]:
        _ = request
        committed_messages.extend(pending_messages)
        return [*history, *pending_messages], [], [], []

    monkeypatch.setattr(
        session_support_module,
        "RecoverableToolInvoker",
        _FakeRecoverableToolInvoker,
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = _FakeEventLog(())
    session.__dict__["_task_repo"] = None
    session.__dict__["_message_repo"] = None
    session.__dict__["_tool_registry"] = object()
    session.__dict__["_commit_all_safe_messages_async"] = (
        _commit_all_safe_messages_async
    )
    deps = cast(ToolDeps, object())

    (
        history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=[],
        deps=deps,
        recover_ready_calls=True,
    )

    assert recovered_count == 1
    assert len(history) == 2
    assert len(committed_messages) == 2
    response = committed_messages[0]
    result_request = committed_messages[1]
    assert isinstance(response, ModelResponse)
    assert isinstance(result_request, ModelRequest)
    assert isinstance(response.parts[0], ToolCallPart)
    assert response.parts[0].tool_call_id == "call-open"
    assert isinstance(result_request.parts[0], ToolReturnPart)
    assert result_request.parts[0].tool_call_id == "call-open"
    assert result_request.parts[0].content == {
        "ok": True,
        "data": {"content": "readme"},
    }
    persisted_state = load_tool_call_state(
        shared_store=shared_store,
        task_id=request.task_id,
        tool_call_id="call-open",
    )
    assert persisted_state is not None
    assert persisted_state.execution_status == ToolExecutionStatus.COMPLETED
    assert persisted_state.result_envelope == {
        "visible_result": {"ok": True, "data": {"content": "readme"}},
        "runtime_meta": {
            "tool_result_durably_recorded": True,
            "tool_result_event_published": False,
            "recovered_tool_call": True,
        },
    }
    assert invoke_calls == [
        {
            "tool_registry": session.__dict__["_tool_registry"],
            "deps": deps,
            "tool_name": "read_file",
            "tool_call_id": "call-open",
            "raw_args": '{"path":"README.md"}',
        }
    ]


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_ignores_unsafe_trailing_tool_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-unsafe-history.db")
    merge_tool_call_batch_state(
        shared_store=shared_store,
        task_id=request.task_id,
        batch_id="batch-unsafe",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        status=ToolCallBatchStatus.SEALED,
        items=(
            PersistedToolCallBatchItem(
                tool_call_id="call-unsafe",
                tool_name="read_file",
                args_preview='{"path":"README.md"}',
                index=0,
            ),
        ),
    )
    merge_tool_call_state(
        shared_store=shared_store,
        task_id=request.task_id,
        tool_call_id="call-unsafe",
        tool_name="read_file",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        args_preview='{"path":"README.md"}',
        execution_status=ToolExecutionStatus.READY,
        batch_id="batch-unsafe",
        batch_index=0,
        batch_size=1,
    )
    unsafe_history: list[ModelRequest | ModelResponse] = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-unsafe",
                )
            ]
        )
    ]
    invoke_calls: list[str] = []

    class _FakeRecoverableToolInvoker:
        async def invoke_async(
            self,
            *,
            tool_registry: object,
            deps: object,
            tool_name: str,
            tool_call_id: str,
            raw_args: object,
        ) -> dict[str, JsonValue]:
            _ = (tool_registry, deps, tool_name, raw_args)
            invoke_calls.append(tool_call_id)
            return {"ok": True, "data": {"content": "readme"}}

    async def _commit_all_safe_messages_async(
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        list[int],
        list[str],
    ]:
        _ = request
        return [*history, *pending_messages], [], [], []

    monkeypatch.setattr(
        session_support_module,
        "RecoverableToolInvoker",
        _FakeRecoverableToolInvoker,
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = _FakeEventLog(())
    session.__dict__["_task_repo"] = None
    session.__dict__["_message_repo"] = _FakeMessageRepo(unsafe_history)
    session.__dict__["_tool_registry"] = object()
    session.__dict__["_commit_all_safe_messages_async"] = (
        _commit_all_safe_messages_async
    )

    (
        recovered_history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=unsafe_history,
        deps=cast(ToolDeps, object()),
        recover_ready_calls=True,
    )

    assert recovered_count == 1
    assert invoke_calls == ["call-unsafe"]
    assert len(recovered_history) == 3
    result_request = recovered_history[-1]
    assert isinstance(result_request, ModelRequest)
    assert isinstance(result_request.parts[0], ToolReturnPart)
    assert result_request.parts[0].tool_call_id == "call-unsafe"


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_skips_item_state_read_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-state-read-failure.db")
    merge_tool_call_batch_state(
        shared_store=shared_store,
        task_id=request.task_id,
        batch_id="batch-read-failure",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        status=ToolCallBatchStatus.SEALED,
        items=(
            PersistedToolCallBatchItem(
                tool_call_id="call-read-failure",
                tool_name="read_file",
                args_preview='{"path":"README.md"}',
                index=0,
            ),
        ),
    )

    def _raise_state_read_failure(
        *,
        shared_store: object,
        event_log: object,
        trace_id: str,
        task_id: str,
        tool_call_id: str,
        task_repo: object,
    ) -> PersistedToolCallState | None:
        _ = (shared_store, event_log, trace_id, task_id, tool_call_id, task_repo)
        raise sqlite3.OperationalError("database is locked")

    async def _commit_all_safe_messages_async(
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        list[int],
        list[str],
    ]:
        _ = (request, history, pending_messages)
        raise AssertionError("batch with unreadable state must not be committed")

    monkeypatch.setattr(
        session_support_module,
        "load_or_recover_tool_call_state",
        _raise_state_read_failure,
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = _FakeEventLog(())
    session.__dict__["_task_repo"] = None
    session.__dict__["_message_repo"] = None
    session.__dict__["_tool_registry"] = object()
    session.__dict__["_commit_all_safe_messages_async"] = (
        _commit_all_safe_messages_async
    )

    (
        recovered_history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=[],
        deps=cast(ToolDeps, object()),
        recover_ready_calls=True,
    )

    assert recovered_history == []
    assert recovered_count == 0


@pytest.mark.asyncio
async def test_recover_uncommitted_tool_batches_recovers_only_missing_batch_items(
    tmp_path: Path,
) -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-mixed-batch-recovery.db")
    merge_tool_call_batch_state(
        shared_store=shared_store,
        task_id=request.task_id,
        batch_id="batch-mixed",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        status=ToolCallBatchStatus.SEALED,
        items=(
            PersistedToolCallBatchItem(
                tool_call_id="call-committed",
                tool_name="read_file",
                args_preview='{"path":"committed.md"}',
                index=0,
            ),
            PersistedToolCallBatchItem(
                tool_call_id="call-missing",
                tool_name="read_file",
                args_preview='{"path":"missing.md"}',
                index=1,
            ),
        ),
    )
    merge_tool_call_state(
        shared_store=shared_store,
        task_id=request.task_id,
        tool_call_id="call-missing",
        tool_name="read_file",
        run_id=request.run_id,
        session_id=request.session_id,
        instance_id=request.instance_id,
        role_id=request.role_id,
        args_preview='{"path":"missing.md"}',
        execution_status=ToolExecutionStatus.COMPLETED,
        batch_id="batch-mixed",
        batch_index=1,
        batch_size=2,
        result_envelope={
            "ok": True,
            "data": {"content": "missing"},
            "meta": {"tool_result_event_published": True},
        },
    )
    committed_history: list[ModelRequest | ModelResponse] = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args={"path": "committed.md"},
                    tool_call_id="call-committed",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-committed",
                    content={"ok": True, "data": {"content": "committed"}},
                )
            ]
        ),
    ]
    committed_messages: list[ModelRequest | ModelResponse] = []

    async def _commit_all_safe_messages_async(
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        list[int],
        list[str],
    ]:
        _ = request
        committed_messages.extend(pending_messages)
        return [*history, *pending_messages], [], [], []

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = _FakeEventLog(())
    session.__dict__["_task_repo"] = None
    session.__dict__["_message_repo"] = _FakeMessageRepo(committed_history)
    session.__dict__["_commit_all_safe_messages_async"] = (
        _commit_all_safe_messages_async
    )

    (
        history,
        recovered_count,
    ) = await AgentLlmSession._recover_uncommitted_tool_batches_async(
        session,
        request=request,
        history=committed_history,
        deps=cast(ToolDeps, object()),
        recover_ready_calls=False,
    )

    assert recovered_count == 1
    assert history == [*committed_history, *committed_messages]
    assert len(committed_messages) == 2
    response = committed_messages[0]
    result_request = committed_messages[1]
    assert isinstance(response, ModelResponse)
    assert isinstance(result_request, ModelRequest)
    assert len(response.parts) == 1
    assert len(result_request.parts) == 1
    assert isinstance(response.parts[0], ToolCallPart)
    assert isinstance(result_request.parts[0], ToolReturnPart)
    assert response.parts[0].tool_call_id == "call-missing"
    assert result_request.parts[0].tool_call_id == "call-missing"


def test_subagent_record_helpers_cover_empty_and_background_paths() -> None:
    session = object.__new__(AgentLlmSession)
    request = _build_request()
    state = PersistedToolCallState.model_construct(
        tool_call_id="",
        tool_name="spawn_subagent",
        instance_id="inst-1",
        role_id="writer",
    )

    class _WaitOnlyService:
        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            return object()

    assert (
        AgentLlmSession._subagent_record_for_tool_state(
            service=cast(
                session_support_module._SubagentWaitService, _WaitOnlyService()
            ),
            request=request,
            state=state,
        )
        is None
    )
    setattr(session, "_background_task_service", _WaitOnlyService())
    assert (
        AgentLlmSession._spawn_subagent_has_durable_launch(
            session,
            request=request,
            state=state,
        )
        is False
    )
    durable = state.model_copy(
        update={"call_state": {"subagent_run_id": "subagent-run-1"}}
    )
    assert AgentLlmSession._spawn_subagent_has_durable_launch(
        session,
        request=request,
        state=durable,
    )
    assert AgentLlmSession._is_background_subagent_recovery(
        call_state={"background": "true"},
        record=None,
    )


@pytest.mark.asyncio
async def test_restore_pending_tool_results_recovers_orphaned_subagent_from_args_preview(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-preview",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        args_preview=json.dumps(
            {
                "role_id": "Explorer",
                "description": "Investigate",
                "prompt": "Recover this subagent call from tool args.",
                "background": False,
            }
        ),
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "tool": "spawn_subagent",
            "visible_result": {
                "ok": True,
                "data": {
                    "completed": True,
                    "output": "preview result",
                },
                "meta": {"tool_result_event_published": True},
            },
            "runtime_meta": {"tool_result_event_published": True},
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-preview",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_message_repo"] = _FakeMessageRepo([])

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert len(recovered_messages) == 2
    recovered_call = recovered_messages[0]
    assert isinstance(recovered_call, ModelResponse)
    assert isinstance(recovered_call.parts[0], ToolCallPart)
    assert recovered_call.parts[0].tool_call_id == "call-subagent-preview"
    assert recovered_call.parts[0].args == {
        "role_id": "Explorer",
        "description": "Investigate",
        "prompt": "Recover this subagent call from tool args.",
        "background": False,
    }
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    assert isinstance(recovered_result.parts[0], ToolReturnPart)
    assert recovered_result.parts[0].content == {
        "ok": True,
        "data": {
            "completed": True,
            "output": "preview result",
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_recovers_subagent_from_summary_args_preview(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-summary",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        args_preview=json.dumps(
            {
                "role_id": "Explorer",
                "background": False,
                "description_len": 11,
                "prompt_len": 44,
            }
        ),
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "tool": "spawn_subagent",
            "visible_result": {
                "ok": True,
                "data": {
                    "completed": True,
                    "output": "summary result",
                },
                "meta": {"tool_result_event_published": True},
            },
            "runtime_meta": {"tool_result_event_published": True},
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-summary",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_message_repo"] = _FakeMessageRepo([])

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    recovered_call = recovered_messages[0]
    assert isinstance(recovered_call, ModelResponse)
    assert isinstance(recovered_call.parts[0], ToolCallPart)
    assert recovered_call.parts[0].args == {
        "role_id": "Explorer",
        "description": "Recovered spawn_subagent call",
        "prompt": "Recovered spawn_subagent prompt unavailable.",
        "background": False,
    }
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    assert isinstance(recovered_result.parts[0], ToolReturnPart)
    assert recovered_result.parts[0].content == {
        "ok": True,
        "data": {
            "completed": True,
            "output": "summary result",
        },
        "meta": {"tool_result_event_published": True},
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_interleaves_recovered_orphaned_subagent_results(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    for index in (1, 2):
        state = PersistedToolCallState(
            tool_call_id=f"call-subagent-{index}",
            tool_name="spawn_subagent",
            run_id="run-1",
            instance_id="inst-1",
            role_id="writer",
            execution_status=ToolExecutionStatus.RUNNING,
            updated_at=f"2026-04-23T00:00:0{index}+00:00",
            call_state={
                "kind": "spawn_subagent_sync",
                "subagent_run_id": f"subagent-run-{index}",
                "requested_role_id": "Explorer",
                "description": "Investigate",
                "prompt": f"Prompt {index}.",
                "background": False,
            },
        )
        shared_store.manage_state(
            StateMutation(
                scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
                key=f"tool_call_state:call-subagent-{index}",
                value_json=state.model_dump_json(),
            )
        )

    class _FakeBackgroundTaskService:
        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            assert parent_run_id == "run-1"
            if subagent_run_id != "subagent-run-1":
                raise KeyError(subagent_run_id)
            return type(
                "_Result",
                (),
                {
                    "run_id": "subagent-run-1",
                    "output": "first result",
                },
            )()

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert len(recovered_messages) == 3
    assert isinstance(recovered_messages[0], ModelResponse)
    assert isinstance(recovered_messages[1], ModelRequest)
    assert isinstance(recovered_messages[2], ModelResponse)
    assert AgentLlmSession._last_committable_index(session, recovered_messages) == 2
    first_result = recovered_messages[1].parts[0]
    assert isinstance(first_result, ToolReturnPart)
    assert first_result.tool_call_id == "call-subagent-1"


@pytest.mark.asyncio
async def test_restore_pending_tool_results_converts_cancelled_subagent_wait_to_tool_error(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-1",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-1",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Prompt.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-1",
            value_json=state.model_dump_json(),
        )
    )

    class _FakeBackgroundTaskService:
        async def wait_for_subagent_run(
            self,
            *,
            parent_run_id: str,
            subagent_run_id: str,
        ) -> object:
            _ = (parent_run_id, subagent_run_id)
            raise asyncio.CancelledError

    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_background_task_service"] = _FakeBackgroundTaskService()

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 1
    assert len(recovered_messages) == 2
    recovered_result = recovered_messages[1]
    assert isinstance(recovered_result, ModelRequest)
    result_part = recovered_result.parts[0]
    assert isinstance(result_part, ToolReturnPart)
    assert result_part.content == {
        "ok": False,
        "error": {
            "code": "subagent_execution_cancelled",
            "message": "Subagent was cancelled during recovery",
        },
    }


@pytest.mark.asyncio
async def test_restore_pending_tool_results_ignores_orphaned_subagent_from_other_run(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-old",
        tool_name="spawn_subagent",
        run_id="run-old",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-old",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Old attempt prompt.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-old",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 0
    assert recovered_messages == []


@pytest.mark.asyncio
async def test_restore_pending_tool_results_ignores_committed_orphaned_subagent_call(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-1",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-1",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Already committed prompt.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-1",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)
    message_repo = _FakeMessageRepo(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="spawn_subagent",
                        tool_call_id="call-subagent-1",
                        args={
                            "role_id": "Explorer",
                            "description": "Investigate",
                            "prompt": "Already committed prompt.",
                            "background": False,
                        },
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="spawn_subagent",
                        tool_call_id="call-subagent-1",
                        content={"ok": True},
                    )
                ]
            ),
        ]
    )
    session.__dict__["_message_repo"] = message_repo

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request().model_copy(update={"conversation_id": ""}),
        pending_messages=[],
    )

    assert recovered_count == 0
    assert recovered_messages == []
    assert message_repo.requested_conversation_ids == ["conv_session_1_writer"]


def test_superseded_tool_call_ids_scope_to_current_instance_and_role() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_event_bus"] = _FakeEventLog(
        (
            {
                "event_type": RunEventType.TOOL_RESULT.value,
                "trace_id": "trace-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "inst-other",
                "role_id": "writer",
                "payload_json": json.dumps(
                    {
                        "tool_name": "spawn_subagent",
                        "tool_call_id": "call-other-instance",
                        "role_id": "writer",
                        "instance_id": "inst-other",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "tool_call_superseded_by_retry",
                                "message": "closed before retry",
                            },
                        },
                    }
                ),
                "occurred_at": "",
            },
            {
                "event_type": RunEventType.TOOL_RESULT.value,
                "trace_id": "trace-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "inst-1",
                "role_id": "other-role",
                "payload_json": json.dumps(
                    {
                        "tool_name": "spawn_subagent",
                        "tool_call_id": "call-other-role",
                        "role_id": "other-role",
                        "instance_id": "inst-1",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "tool_call_superseded_by_retry",
                                "message": "closed before retry",
                            },
                        },
                    }
                ),
                "occurred_at": "",
            },
            {
                "event_type": RunEventType.TOOL_RESULT.value,
                "trace_id": "trace-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "inst-1",
                "role_id": "writer",
                "payload_json": json.dumps(
                    {
                        "tool_name": "spawn_subagent",
                        "tool_call_id": "call-current",
                        "role_id": "writer",
                        "instance_id": "inst-1",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "tool_call_superseded_by_retry",
                                "message": "closed before retry",
                            },
                        },
                    }
                ),
                "occurred_at": "",
            },
        )
    )

    superseded_ids = AgentLlmSession._superseded_tool_call_ids_for_request(
        session,
        _build_request(),
    )

    assert superseded_ids == {"call-current"}


def test_best_effort_replay_lookups_ignore_sqlite_read_failures() -> None:
    request = _build_request()
    session = object.__new__(AgentLlmSession)

    class _FailingEventBus:
        def list_by_trace(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
            _ = trace_id
            raise sqlite3.OperationalError("database is locked")

    class _FailingMessageRepo:
        def get_history_for_conversation(
            self,
            conversation_id: str,
        ) -> list[ModelRequest | ModelResponse]:
            _ = conversation_id
            raise sqlite3.DatabaseError("database is locked")

    session.__dict__["_event_bus"] = _FailingEventBus()
    session.__dict__["_message_repo"] = _FailingMessageRepo()

    assert (
        AgentLlmSession._superseded_tool_call_ids_for_request(session, request) == set()
    )
    assert (
        AgentLlmSession._committed_tool_call_ids_for_request(session, request) == set()
    )


@pytest.mark.asyncio
async def test_restore_pending_tool_results_ignores_superseded_orphaned_subagent_call(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-1",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-1",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Already superseded prompt.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-1",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    event_log = _FakeEventLog(
        (
            {
                "event_type": RunEventType.TOOL_RESULT.value,
                "trace_id": "trace-1",
                "session_id": "session-1",
                "task_id": "task-1",
                "instance_id": "inst-1",
                "payload_json": json.dumps(
                    {
                        "tool_name": "spawn_subagent",
                        "tool_call_id": "call-subagent-1",
                        "result": {
                            "ok": False,
                            "error": {
                                "code": "tool_call_superseded_by_retry",
                                "message": "closed before retry",
                            },
                        },
                    }
                ),
                "occurred_at": "",
            },
        )
    )
    session.__dict__["_event_bus"] = event_log
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_message_repo"] = _FakeMessageRepo([])

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 0
    assert recovered_messages == []
    assert event_log.requested_trace_ids == ["trace-1"]


@pytest.mark.asyncio
async def test_restore_pending_tool_results_ignores_orphaned_subagent_from_other_instance(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-other-instance",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-other",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "subagent_run_id": "subagent-run-other",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Other instance prompt.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-other-instance",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 0
    assert recovered_messages == []


@pytest.mark.asyncio
async def test_restore_pending_tool_results_keeps_orphaned_subagent_without_result(
    tmp_path: Path,
) -> None:
    session = object.__new__(AgentLlmSession)
    shared_store = SharedStateRepository(tmp_path / "session-support-state.db")
    state = PersistedToolCallState(
        tool_call_id="call-subagent-1",
        tool_name="spawn_subagent",
        run_id="run-1",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.RUNNING,
        call_state={
            "kind": "spawn_subagent_sync",
            "requested_role_id": "Explorer",
            "description": "Investigate",
            "prompt": "Inspect the failing tests and summarize the root cause.",
            "background": False,
        },
    )
    shared_store.manage_state(
        StateMutation(
            scope=ScopeRef(scope_type=ScopeType.TASK, scope_id="task-1"),
            key="tool_call_state:call-subagent-1",
            value_json=state.model_dump_json(),
        )
    )
    session.__dict__["_shared_store"] = shared_store
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    (
        recovered_messages,
        recovered_count,
    ) = await AgentLlmSession._restore_pending_tool_results_from_state(
        session,
        request=_build_request(),
        pending_messages=[],
    )

    assert recovered_count == 0
    assert len(recovered_messages) == 1
    recovered_call = recovered_messages[0]
    assert isinstance(recovered_call, ModelResponse)
    assert isinstance(recovered_call.parts[0], ToolCallPart)
    assert recovered_call.parts[0].tool_name == "spawn_subagent"
    assert recovered_call.parts[0].tool_call_id == "call-subagent-1"
