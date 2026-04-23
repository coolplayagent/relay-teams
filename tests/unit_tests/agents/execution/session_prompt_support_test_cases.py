# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from relay_teams.agents.execution import session_prompt as session_prompt_module

from .agent_llm_session_test_support import (
    AgentLlmSession,
    AssistantRunError,
    ConversationCompactionService,
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    McpConfigScope,
    McpRegistry,
    McpServerSpec,
    MessageRepository,
    ModelEndpointConfig,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    RunEvent,
    RunEventType,
    ToolCallPart,
    ToolExecutionStatus,
    ToolReturnPart,
    UserPromptPart,
    _FakeCompactionService,
    _FakeMessageRepo,
    _FakePromptHookService,
    _FakeRunEnvHookService,
    _build_request,
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
                    '{"command":"python -c \\"print(\\\'hello\\\')\\""'
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

    normalized = AgentLlmSession._normalize_committable_messages(
        session,
        request=_build_request(),
        messages=[request],
    )

    assert len(normalized) == 1
    normalized_request = normalized[0]
    assert isinstance(normalized_request, ModelRequest)
    assert normalized_request.instructions == "System instructions"
    assert normalized_request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert normalized_request.run_id == "run-123"
    assert normalized_request.metadata == {"source": "test"}


def test_normalize_committable_messages_rewrites_retry_prompt_to_tool_result() -> None:
    session = object.__new__(AgentLlmSession)
    request = ModelRequest(
        parts=[
            RetryPromptPart(
                content="missing required field",
                tool_name="shell",
                tool_call_id="call-1",
            )
        ]
    )

    normalized = AgentLlmSession._normalize_committable_messages(
        session,
        request=_build_request(),
        messages=[request],
    )

    assert len(normalized) == 1
    normalized_request = normalized[0]
    assert isinstance(normalized_request, ModelRequest)
    assert normalized_request is not request
    assert len(normalized_request.parts) == 1
    normalized_part = normalized_request.parts[0]
    assert isinstance(normalized_part, ToolReturnPart)
    assert normalized_part.tool_name == "shell"
    assert normalized_part.tool_call_id == "call-1"
    assert normalized_part.content == {
        "ok": False,
        "error": {
            "code": "tool_input_validation_failed",
            "message": "missing required field",
        },
    }


def test_normalize_committable_messages_keeps_tool_return_metadata() -> None:
    session = object.__new__(AgentLlmSession)

    normalized = AgentLlmSession._normalize_committable_messages(
        session,
        request=_build_request(),
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="read",
                        tool_call_id="call-read-1",
                        content={"ok": True, "data": {"type": "image"}},
                        metadata={"keep": "me"},
                    )
                ]
            )
        ],
    )

    request_message = normalized[0]
    assert isinstance(request_message, ModelRequest)
    tool_return = request_message.parts[0]
    assert isinstance(tool_return, ToolReturnPart)
    assert tool_return.metadata == {"keep": "me"}


def test_last_committable_index_stops_before_open_tool_call() -> None:
    session = object.__new__(AgentLlmSession)
    messages: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[UserPromptPart(content="User prompt")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="search",
                    args='{"q":"moon"}',
                    tool_call_id="call-1",
                )
            ]
        ),
    ]

    safe_index = AgentLlmSession._last_committable_index(session, messages)

    assert safe_index == 1
    assert AgentLlmSession._has_pending_tool_calls(session, messages) is True


def test_commit_ready_messages_commits_only_safe_prefix() -> None:
    session = object.__new__(AgentLlmSession)
    persisted_history: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[UserPromptPart(content="User prompt")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="search",
                    args='{"q":"moon"}',
                    tool_call_id="call-1",
                )
            ]
        ),
    ]
    message_repo = _FakeMessageRepo(history=persisted_history)
    published_outcome_messages: list[list[ModelRequest | ModelResponse]] = []
    session.__dict__["_message_repo"] = cast(MessageRepository, message_repo)
    session.__dict__["_publish_committed_tool_outcome_events_from_messages"] = (
        lambda **kwargs: published_outcome_messages.append(
            cast(list[ModelRequest | ModelResponse], kwargs["messages"])
        )
    )

    next_history, remaining, tool_events_published, validation_failures = (
        AgentLlmSession._commit_ready_messages(
            session,
            request=_build_request(),
            history=[],
            pending_messages=[
                ModelRequest(parts=[UserPromptPart(content="User prompt")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="search",
                            args='{"q":"moon"}',
                            tool_call_id="call-1",
                        )
                    ]
                ),
            ],
        )
    )

    assert len(message_repo.append_calls) == 1
    appended_messages = message_repo.append_calls[0]
    assert len(appended_messages) == 1
    assert isinstance(appended_messages[0], ModelRequest)
    assert len(appended_messages[0].parts) == 1
    appended_part = appended_messages[0].parts[0]
    assert isinstance(appended_part, UserPromptPart)
    assert appended_part.content == "User prompt"
    assert published_outcome_messages == [appended_messages]
    assert next_history == persisted_history
    assert len(remaining) == 1
    assert isinstance(remaining[0], ModelResponse)
    assert tool_events_published is False
    assert validation_failures is False


def test_inject_compaction_summary_returns_original_without_compaction_service() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._conversation_compaction_service = None

    assert (
        AgentLlmSession._inject_compaction_summary(
            session,
            session_id="session-1",
            conversation_id="conv-1",
            system_prompt="System prompt",
        )
        == "System prompt"
    )


def test_inject_compaction_summary_ignores_empty_prompt_section() -> None:
    session = object.__new__(AgentLlmSession)
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        _FakeCompactionService(prompt_section=""),
    )

    assert (
        AgentLlmSession._inject_compaction_summary(
            session,
            session_id="session-1",
            conversation_id="conv-1",
            system_prompt="System prompt",
        )
        == "System prompt"
    )


def test_inject_compaction_summary_appends_prompt_section() -> None:
    session = object.__new__(AgentLlmSession)
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        _FakeCompactionService(prompt_section="## Summary\nKeep prior work."),
    )

    combined = AgentLlmSession._inject_compaction_summary(
        session,
        session_id="session-1",
        conversation_id="conv-1",
        system_prompt="System prompt",
    )

    assert combined == "System prompt\n\n## Summary\nKeep prior work."


def test_publish_tool_call_events_from_messages_deduplicates_tool_call_ids() -> None:
    session = object.__new__(AgentLlmSession)
    published_events: list[RunEvent] = []
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub",
        (),
        {"publish": lambda self, event: published_events.append(event)},
    )()

    emitted = AgentLlmSession._publish_tool_call_events_from_messages(
        session,
        request=_build_request(),
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search",
                        args='{"q":"moon"}',
                        tool_call_id="call-1",
                    ),
                    ToolCallPart(
                        tool_name="search",
                        args='{"q":"moon"}',
                        tool_call_id="call-1",
                    ),
                ]
            )
        ],
        published_tool_call_ids={"call-1"},
    )

    assert emitted is False
    assert published_events == []

    next_emitted = AgentLlmSession._publish_tool_call_events_from_messages(
        session,
        request=_build_request(),
        messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="search",
                        args='{"q":"mars"}',
                        tool_call_id="call-2",
                    )
                ]
            )
        ],
        published_tool_call_ids={"call-1"},
    )

    assert next_emitted is True
    assert len(published_events) == 1
    assert published_events[0].event_type == RunEventType.TOOL_CALL
    assert json.loads(cast(str, published_events[0].payload_json)) == {
        "tool_name": "search",
        "tool_call_id": "call-2",
        "args": '{"q":"mars"}',
        "role_id": "writer",
        "instance_id": "inst-1",
    }


def test_publish_committed_tool_outcome_events_emits_result_and_validation_failure() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_tool_result_already_emitted_from_runtime"] = lambda **kwargs: (
        False
    )
    published_events: list[RunEvent] = []
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub",
        (),
        {"publish": lambda self, event: published_events.append(event)},
    )()

    AgentLlmSession._publish_committed_tool_outcome_events_from_messages(
        session,
        request=_build_request(),
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="search",
                        tool_call_id="call-1",
                        content={"ok": False, "error": "boom"},
                    ),
                    RetryPromptPart(
                        content="missing required field",
                        tool_name="shell",
                        tool_call_id="call-2",
                    ),
                ]
            )
        ],
    )

    assert len(published_events) == 2
    assert published_events[0].event_type == RunEventType.TOOL_RESULT
    assert json.loads(cast(str, published_events[0].payload_json)) == {
        "tool_name": "search",
        "tool_call_id": "call-1",
        "result": {"ok": False, "error": "boom"},
        "error": True,
        "role_id": "writer",
        "instance_id": "inst-1",
    }
    assert published_events[1].event_type == RunEventType.TOOL_INPUT_VALIDATION_FAILED
    assert json.loads(cast(str, published_events[1].payload_json)) == {
        "tool_name": "shell",
        "tool_call_id": "call-2",
        "reason": "Input validation failed before tool execution.",
        "details": "missing required field",
        "role_id": "writer",
        "instance_id": "inst-1",
    }


def test_tool_result_already_emitted_from_runtime_uses_persisted_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .agent_llm_session_test_support import PersistedToolCallState

    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    persisted_state = PersistedToolCallState(
        tool_call_id="call-1",
        tool_name="search",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "visible_result": {"ok": True},
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    monkeypatch.setattr(
        session_prompt_module,
        "load_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    already_emitted = AgentLlmSession._tool_result_already_emitted_from_runtime(
        session,
        request=_build_request(),
        tool_name="search",
        tool_call_id="call-1",
    )

    assert already_emitted is True


@pytest.mark.asyncio
async def test_build_agent_iteration_context_does_not_override_proxy_env_when_hook_run_env_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5.1",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
    )
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_hook_service"] = _FakeRunEnvHookService({})

    async def _prepare_prompt_context(**_kwargs: object) -> object:
        return type(
            "_PreparedPrompt",
            (),
            {"history": (), "system_prompt": "Prepared system prompt"},
        )()

    async def _build_model_settings(**_kwargs: object) -> object:
        return object()

    session.__dict__["_prepare_prompt_context"] = _prepare_prompt_context
    session.__dict__["_build_model_settings"] = _build_model_settings

    captured: dict[str, object] = {}

    def _fake_build_coordination_agent(**kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        session_prompt_module,
        "build_coordination_agent",
        _fake_build_coordination_agent,
    )

    _ = await AgentLlmSession._build_agent_iteration_context(
        session,
        request=_build_request(),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=False,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert captured["merged_env"] is None


@pytest.mark.asyncio
async def test_apply_user_prompt_hooks_rewrites_prompt_and_adds_context() -> None:
    session = object.__new__(AgentLlmSession)
    hook_service = _FakePromptHookService(
        HookDecisionBundle(
            decision=HookDecisionType.UPDATED_INPUT,
            updated_input="Rewritten prompt",
            additional_context=("Hook context",),
        )
    )
    setattr(session, "_hook_service", cast(Any, hook_service))
    setattr(session, "_run_event_hub", cast(Any, None))

    request, context = await AgentLlmSession._apply_user_prompt_hooks(
        session,
        _build_request(user_prompt="Original prompt"),
    )

    assert request.user_prompt == "Rewritten prompt"
    assert request.input == ()
    assert context == ("Hook context",)
    assert hook_service.events == [HookEventName.USER_PROMPT_SUBMIT]


@pytest.mark.asyncio
async def test_apply_user_prompt_hooks_uses_latest_persisted_prompt_when_request_is_empty() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    hook_service = _FakePromptHookService(
        HookDecisionBundle(
            decision=HookDecisionType.UPDATED_INPUT,
            updated_input="Rewritten prompt",
        )
    )
    setattr(session, "_hook_service", cast(Any, hook_service))
    setattr(session, "_run_event_hub", cast(Any, None))
    setattr(
        session,
        "_message_repo",
        cast(
            MessageRepository,
            _FakeMessageRepo(
                history=[
                    ModelRequest(
                        parts=[UserPromptPart(content="Original persisted prompt")]
                    )
                ]
            ),
        ),
    )

    request, context = await AgentLlmSession._apply_user_prompt_hooks(
        session,
        _build_request(user_prompt=None),
    )

    assert request.user_prompt == "Rewritten prompt"
    assert context == ()
    assert hook_service.events == [HookEventName.USER_PROMPT_SUBMIT]


@pytest.mark.asyncio
async def test_apply_user_prompt_hooks_raises_when_prompt_denied() -> None:
    session = object.__new__(AgentLlmSession)
    hook_service = _FakePromptHookService(
        HookDecisionBundle(
            decision=HookDecisionType.DENY,
            reason="Prompt blocked by policy.",
        )
    )
    setattr(session, "_hook_service", cast(Any, hook_service))
    setattr(session, "_run_event_hub", cast(Any, None))

    with pytest.raises(AssistantRunError) as exc_info:
        await AgentLlmSession._apply_user_prompt_hooks(
            session,
            _build_request(user_prompt="Blocked prompt"),
        )

    assert exc_info.value.payload.error_code == "prompt_denied"
    assert exc_info.value.payload.error_message == "Prompt blocked by policy."
