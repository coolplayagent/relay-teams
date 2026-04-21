# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json

import httpx
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from openai import APIStatusError

import relay_teams.agents.execution.llm_session as llm_module
from relay_teams.agents.execution.llm_session import (
    AgentLlmSession,
    _FallbackAttemptState,
    _FallbackAttemptStatus,
)
from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
    ConversationMicrocompactResult,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.media import InlineMediaContentPart, MediaModality
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.llm_retry import LlmRetryErrorInfo, LlmRetrySchedule
from relay_teams.providers.model_config import (
    LlmRetryConfig,
    ModelCapabilities,
    ModelEndpointConfig,
    ModelInputCapabilities,
)
from relay_teams.providers.model_fallback import LlmFallbackDecision
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
)
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.assistant_errors import AssistantRunError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
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

    normalized = AgentLlmSession._normalize_committable_messages(session, [request])

    assert len(normalized) == 1
    normalized_request = normalized[0]
    assert isinstance(normalized_request, ModelRequest)
    assert normalized_request.instructions == "System instructions"
    assert normalized_request.timestamp == datetime(2026, 4, 2, 22, 44, 3, tzinfo=UTC)
    assert normalized_request.run_id == "run-123"
    assert normalized_request.metadata == {"source": "test"}


def test_validate_request_input_capabilities_rejects_known_unsupported_image() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-4o-mini",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(input=ModelInputCapabilities(image=False)),
    )
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="writer",
        system_prompt="You are helpful.",
        user_prompt=None,
        input=(
            InlineMediaContentPart(
                modality=MediaModality.IMAGE,
                mime_type="image/png",
                base64_data="QUJD",
                name="diagram.png",
            ),
        ),
    )

    with pytest.raises(RuntimeError, match="does not support image input"):
        AgentLlmSession._validate_request_input_capabilities(session, request)


def test_validate_request_input_capabilities_rejects_unknown_image_support() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="custom-model",
        base_url="https://example.test/v1",
        api_key="secret",
    )
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="writer",
        system_prompt="You are helpful.",
        user_prompt=None,
        input=(
            InlineMediaContentPart(
                modality=MediaModality.IMAGE,
                mime_type="image/png",
                base64_data="QUJD",
                name="diagram.png",
            ),
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="Cannot confirm whether model custom-model supports image input",
    ):
        AgentLlmSession._validate_request_input_capabilities(session, request)


def test_validate_request_input_capabilities_allows_supported_image() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-4.1",
        base_url="https://example.test/v1",
        api_key="secret",
        capabilities=ModelCapabilities(input=ModelInputCapabilities(image=True)),
    )
    request = LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="workspace-1",
        instance_id="instance-1",
        role_id="writer",
        system_prompt="You are helpful.",
        user_prompt=None,
        input=(
            InlineMediaContentPart(
                modality=MediaModality.IMAGE,
                mime_type="image/png",
                base64_data="QUJD",
                name="diagram.png",
            ),
        ),
    )

    AgentLlmSession._validate_request_input_capabilities(session, request)


class _FakeMessageRepo:
    def __init__(self, history: list[ModelRequest | ModelResponse]) -> None:
        self._history = history
        self.append_calls: list[list[ModelRequest | ModelResponse]] = []
        self.pruned_conversation_ids: list[str] = []

    def get_history_for_conversation(
        self,
        _conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        return list(self._history)

    def prune_conversation_history_to_safe_boundary(self, conversation_id: str) -> None:
        self.pruned_conversation_ids.append(conversation_id)

    def append(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: list[ModelRequest | ModelResponse],
    ) -> None:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
        )
        self.append_calls.append(list(messages))

    def replace_pending_user_prompt(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
    ) -> bool:
        _ = (
            session_id,
            workspace_id,
            conversation_id,
            agent_role_id,
            instance_id,
            task_id,
            trace_id,
            content,
        )
        return False


class _FakeMicrocompactService:
    def __init__(self, result: ConversationMicrocompactResult) -> None:
        self.calls: list[object] = []
        self._result = result

    def apply(
        self, *, history: list[ModelRequest | ModelResponse], budget: object
    ) -> ConversationMicrocompactResult:
        self.calls.append((list(history), budget))
        return self._result


class _FakeCompactionService:
    def __init__(self, prompt_section: str = "") -> None:
        self.calls: list[dict[str, object]] = []
        self._prompt_section = prompt_section

    async def maybe_compact(
        self, **kwargs: object
    ) -> list[ModelRequest | ModelResponse]:
        self.calls.append(dict(kwargs))
        history = kwargs["history"]
        assert isinstance(history, list)
        return history

    def build_prompt_section(
        self,
        *,
        session_id: str,
        conversation_id: str,
    ) -> str:
        _ = (session_id, conversation_id)
        return self._prompt_section


class _FakeRunIntentRepo:
    def __init__(self, intent: str) -> None:
        self._intent = intent

    def get(self, run_id: str, *, fallback_session_id: str | None = None) -> object:
        _ = (run_id, fallback_session_id)
        return type("_Intent", (), {"intent": self._intent})()


async def _zero_mcp_context_tokens(
    *,
    allowed_mcp_servers: tuple[str, ...],
) -> int:
    _ = allowed_mcp_servers
    return 0


def _build_request(*, user_prompt: str | None = "User prompt") -> LLMRequest:
    return LLMRequest(
        run_id="run-1",
        trace_id="trace-1",
        task_id="task-1",
        session_id="session-1",
        workspace_id="default",
        conversation_id="conv-1",
        instance_id="inst-1",
        role_id="writer",
        system_prompt="System prompt",
        user_prompt=user_prompt,
    )


@pytest.mark.asyncio
async def test_prepare_prompt_context_applies_microcompact_before_full_compaction() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    base_history = [
        ModelRequest(parts=[UserPromptPart(content="summarize the file")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="A" * 4000,
                )
            ]
        ),
    ]
    microcompacted_history = [
        base_history[0],
        base_history[1],
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="[Compacted tool result]",
                )
            ]
        ),
    ]
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(MessageRepository, _FakeMessageRepo(base_history))
    microcompact_service = _FakeMicrocompactService(
        ConversationMicrocompactResult(
            messages=tuple(microcompacted_history),
            estimated_tokens_before=260,
            estimated_tokens_after=80,
            compacted_message_count=1,
            compacted_part_count=1,
        )
    )
    compaction_service = _FakeCompactionService(
        prompt_section="## Compacted Conversation Summary\nsummary"
    )
    session._conversation_microcompact_service = cast(
        ConversationMicrocompactService,
        microcompact_service,
    )
    session._conversation_compaction_service = cast(
        ConversationCompactionService,
        compaction_service,
    )
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Summarize the file and preserve tool outputs."),
    )
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert list(prepared.history) == microcompacted_history
    assert prepared.system_prompt.endswith("summary")
    assert microcompact_service.calls
    assert compaction_service.calls
    compaction_call = compaction_service.calls[0]
    assert compaction_call["history"] == microcompacted_history
    assert compaction_call["source_history"] == base_history
    assert compaction_call["estimated_tokens_before_microcompact"] == 260
    assert compaction_call["estimated_tokens_after_microcompact"] == 80


@pytest.mark.asyncio
async def test_prepare_prompt_context_inserts_replay_bridge_for_resume_history() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    base_history = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="README contents",
                )
            ]
        ),
    ]
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session._message_repo = cast(MessageRepository, _FakeMessageRepo(base_history))
    session._conversation_microcompact_service = None
    session._conversation_compaction_service = None
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Build the release handoff and keep prior artifacts."),
    )

    prepared = await AgentLlmSession._prepare_prompt_context(
        session,
        request=_build_request(user_prompt=None),
        conversation_id="conv-1",
        system_prompt="System prompt",
        reserve_user_prompt_tokens=False,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    prepared_history = list(prepared.history)
    assert len(prepared_history) == 3
    bridge_message = prepared_history[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Original task intent:" in bridge_part.content
    assert "Build the release handoff" in bridge_part.content
    assert prepared_history[1:] == base_history


def test_coerce_history_to_provider_safe_sequence_drops_orphan_tool_prefix() -> None:
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Investigate the preserved tool execution state."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="README contents",
                )
            ]
        ),
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt=None),
        history=history,
    )

    assert len(repaired) == 3
    bridge_message = repaired[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Investigate the preserved tool execution state." in bridge_part.content
    assert repaired[1:] == history[1:]


def test_coerce_history_to_provider_safe_sequence_keeps_bridge_when_prefix_drop_empties_history() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Resume the preserved execution state after repair."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        )
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt=None),
        history=history,
    )

    assert len(repaired) == 1
    bridge_message = repaired[0]
    assert isinstance(bridge_message, ModelRequest)
    bridge_part = bridge_message.parts[0]
    assert isinstance(bridge_part, UserPromptPart)
    assert "Resume the preserved execution state after repair." in bridge_part.content


def test_coerce_history_to_provider_safe_sequence_prefers_explicit_user_prompt_over_bridge() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._run_intent_repo = cast(
        RunIntentRepository,
        _FakeRunIntentRepo("Resume the preserved execution state after repair."),
    )
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="missing-call",
                    content="orphaned",
                )
            ]
        )
    ]

    repaired = AgentLlmSession._coerce_history_to_provider_safe_sequence(
        session,
        request=_build_request(user_prompt="restart from the latest user request"),
        history=history,
    )

    assert repaired == []


@pytest.mark.asyncio
async def test_safe_max_output_tokens_accounts_for_full_prompt_budget() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=500,
    )
    session._config.sampling.max_tokens = 400
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    max_tokens = await AgentLlmSession._safe_max_output_tokens(
        session,
        request=_build_request(user_prompt="U" * 240),
        history=[ModelRequest(parts=[UserPromptPart(content="hello")])],
        system_prompt="System prompt " + ("S" * 240),
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert max_tokens is not None
    assert 1 <= max_tokens < 400


def test_persist_user_prompt_keeps_microcompacted_history_in_memory() -> None:
    session = object.__new__(AgentLlmSession)
    compacted_history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="[Compacted tool result]\ntool: read_file",
                )
            ]
        )
    ]
    message_repo = _FakeMessageRepo(history=[])
    session._message_repo = cast(MessageRepository, message_repo)

    next_history, rebuild_context = AgentLlmSession._persist_user_prompt_if_needed(
        session,
        request=_build_request(user_prompt="new prompt"),
        history=list(compacted_history),
        content="new prompt",
    )

    assert rebuild_context is False
    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    assert next_history[:-1] == compacted_history
    appended_message = next_history[-1]
    assert isinstance(appended_message, ModelRequest)
    appended_part = appended_message.parts[0]
    assert isinstance(appended_part, UserPromptPart)
    assert appended_part.content == "new prompt"


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


@pytest.mark.asyncio
async def test_generate_async_passes_retry_after_to_retry_schedule() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=600,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=2,
        initial_delay_ms=2000,
    )
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: True

    captured_schedules: list[LlmRetrySchedule] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        captured_schedules.append(cast(LlmRetrySchedule, kwargs["schedule"]))
        raise RuntimeError("stop after scheduling retry")

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "7"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limited", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after scheduling retry"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(captured_schedules) == 1
    schedule = captured_schedules[0]
    assert schedule.delay_ms == 7000


class _FakePromptHookService:
    def __init__(self, bundle: HookDecisionBundle) -> None:
        self.bundle = bundle
        self.events: list[HookEventName] = []

    async def execute(
        self, *, event_input: object, run_event_hub: object
    ) -> HookDecisionBundle:
        _ = run_event_hub
        self.events.append(cast(HookEventName, getattr(event_input, "event_name")))
        return self.bundle


class _FakeRunEnvHookService:
    def __init__(self, run_env: dict[str, str]) -> None:
        self._run_env = run_env

    def get_run_env(self, run_id: str) -> dict[str, str]:
        _ = run_id
        return dict(self._run_env)


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
        "relay_teams.agents.execution.llm_session.build_coordination_agent",
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


@pytest.mark.asyncio
async def test_execute_attempt_recovery_clears_cached_transport_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=1,
        initial_delay_ms=1,
    )
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == "run-1"
        cleared.append("cleared")

    monkeypatch.setattr(
        llm_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)

    async def _fast_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)

    scheduled: list[LlmRetrySchedule] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        scheduled.append(cast(LlmRetrySchedule, kwargs["schedule"]))

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled

    async def _generate_async(
        request: LLMRequest,
        **kwargs: object,
    ) -> str:
        _ = (request, kwargs)
        assert cleared == ["cleared"]
        return "after retry"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="TLS handshake failed",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=True,
        should_resume_after_tool_outcomes=False,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "after retry"
    assert scheduled[0].delay_ms == 0
    assert cleared == ["cleared"]


@pytest.mark.asyncio
async def test_execute_attempt_recovery_keeps_cached_transport_for_non_transport_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(
        jitter=False,
        max_retries=1,
        initial_delay_ms=1,
    )
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == "run-1"
        cleared.append("cleared")

    monkeypatch.setattr(
        llm_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )
    monkeypatch.setattr(llm_module, "compute_retry_delay_ms", lambda **_: 0)

    async def _fast_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr(llm_module.asyncio, "sleep", _fast_sleep)

    async def _ignore_retry_schedule(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_handle_retry_scheduled"] = _ignore_retry_schedule

    async def _generate_async(
        request: LLMRequest,
        **kwargs: object,
    ) -> str:
        _ = (request, kwargs)
        assert cleared == []
        return "after retry"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="slow down",
            status_code=429,
            error_code="rate_limited",
            retryable=True,
            rate_limited=True,
            transport_error=False,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=True,
        should_resume_after_tool_outcomes=False,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "after retry"
    assert cleared == []


@pytest.mark.asyncio
async def test_execute_attempt_recovery_clears_cached_transport_before_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )

    cleared: list[str] = []

    async def _reset_llm_http_client_cache_entry(**kwargs: object) -> None:
        assert kwargs["cache_scope"] == "run-1"
        cleared.append("cleared")

    monkeypatch.setattr(
        llm_module,
        "reset_llm_http_client_cache_entry",
        _reset_llm_http_client_cache_entry,
    )

    async def _resume_after_tool_outcomes(**kwargs: object) -> str:
        _ = kwargs
        assert cleared == ["cleared"]
        return "resumed"

    session.__dict__["_resume_after_tool_outcomes"] = _resume_after_tool_outcomes

    result = await AgentLlmSession._execute_attempt_recovery(
        session,
        request=_build_request(),
        retry_error=LlmRetryErrorInfo(
            message="TLS handshake failed",
            error_code="network_error",
            retryable=True,
            transport_error=True,
        ),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[],
        should_retry=False,
        should_resume_after_tool_outcomes=True,
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        fallback_state=_FallbackAttemptState.initial("default"),
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "resumed"
    assert cleared == ["cleared"]


def test_restore_pending_tool_results_from_state_backfills_completed_dispatch_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    persisted_state = PersistedToolCallState(
        tool_call_id="call-dispatch-1",
        tool_name="dispatch_task",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "tool": "dispatch_task",
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
        llm_module,
        "load_or_recover_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    recovered_messages, recovered_count = (
        AgentLlmSession._restore_pending_tool_results_from_state(
            session,
            request=_build_request(),
            pending_messages=[
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="dispatch_task",
                            args='{"task_id":"task-child-1","role_id":"Crafter"}',
                            tool_call_id="call-dispatch-1",
                        )
                    ]
                )
            ],
        )
    )

    assert recovered_count == 1
    assert len(recovered_messages) == 2
    synthetic_request = recovered_messages[-1]
    assert isinstance(synthetic_request, ModelRequest)
    assert len(synthetic_request.parts) == 1
    recovered_part = synthetic_request.parts[0]
    assert isinstance(recovered_part, ToolReturnPart)
    assert recovered_part.tool_name == "dispatch_task"
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


def test_publish_committed_tool_outcome_events_skips_visible_only_recovered_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    published_events: list[object] = []
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub",
        (),
        {"publish": lambda self, event: published_events.append(event)},
    )()

    persisted_state = PersistedToolCallState(
        tool_call_id="call-dispatch-1",
        tool_name="dispatch_task",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "ok": True,
            "data": {"task": {"task_id": "task-child-1", "status": "completed"}},
            "meta": {"tool_result_event_published": True},
        },
    )

    monkeypatch.setattr(
        llm_module,
        "load_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    AgentLlmSession._publish_committed_tool_outcome_events_from_messages(
        session,
        request=_build_request(),
        messages=[
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="dispatch_task",
                        tool_call_id="call-dispatch-1",
                        content={
                            "ok": True,
                            "data": {
                                "task": {
                                    "task_id": "task-child-1",
                                    "status": "completed",
                                }
                            },
                        },
                    )
                ]
            )
        ],
    )

    assert published_events == []


@pytest.mark.asyncio
async def test_resume_after_tool_outcomes_commits_backfilled_tool_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_task_repo"] = cast(object, None)

    persisted_state = PersistedToolCallState(
        tool_call_id="call-dispatch-1",
        tool_name="dispatch_task",
        instance_id="inst-1",
        role_id="writer",
        execution_status=ToolExecutionStatus.COMPLETED,
        result_envelope={
            "visible_result": {
                "ok": True,
                "data": {"task": {"task_id": "task-child-1", "status": "completed"}},
                "meta": {"tool_result_event_published": True},
            },
            "runtime_meta": {"tool_result_event_published": True},
        },
    )

    monkeypatch.setattr(
        llm_module,
        "load_or_recover_tool_call_state",
        lambda **kwargs: persisted_state,
    )

    captured_pending_messages: list[ModelRequest | ModelResponse] = []

    def _capture_commit_all_safe_messages(**kwargs: object):
        pending_messages = kwargs["pending_messages"]
        assert isinstance(pending_messages, list)
        captured_pending_messages.extend(
            cast(list[ModelRequest | ModelResponse], pending_messages)
        )
        synthetic_request = cast(
            ModelRequest,
            cast(list[ModelRequest | ModelResponse], pending_messages)[-1],
        )
        recovered_part = synthetic_request.parts[0]
        assert isinstance(recovered_part, ToolReturnPart)
        assert recovered_part.tool_call_id == "call-dispatch-1"
        return [], [], False, False

    session.__dict__["_commit_all_safe_messages"] = _capture_commit_all_safe_messages
    session.__dict__["_publish_synthetic_tool_results_for_pending_calls"] = (
        lambda **kwargs: 0
    )

    async def _generate_async(*args: object, **kwargs: object) -> str:
        _ = (args, kwargs)
        return "resumed"

    session.__dict__["_generate_async"] = _generate_async

    result = await AgentLlmSession._resume_after_tool_outcomes(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=2,
        history=[],
        pending_messages=[
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="dispatch_task",
                        args='{"task_id":"task-child-1","role_id":"Crafter"}',
                        tool_call_id="call-dispatch-1",
                    )
                ]
            )
        ],
        fallback_state=_FallbackAttemptState.initial("default"),
    )

    assert result == "resumed"
    assert len(captured_pending_messages) == 2


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_cancellation() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )

    class _CancelledControlContext:
        def raise_if_cancelled(self) -> None:
            raise asyncio.CancelledError()

    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: _CancelledControlContext(),
        },
    )()

    class _UnusedAgent:
        def iter(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("cancelled runs should not start agent iteration")

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _UnusedAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: LLMRequest) -> None:
        closed_run_ids.append(request.run_id)

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_generate_async_closes_scoped_transport_cache_on_setup_failure() -> None:
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="glm-5",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        api_key="test-key",
        connect_timeout_seconds=15.0,
    )
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )

    async def _build_agent_iteration_context(**kwargs: object) -> object:
        _ = kwargs
        raise RuntimeError("setup failed after creating scoped client")

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    closed_run_ids: list[str] = []

    async def _close_run_scoped_llm_http_client(*, request: LLMRequest) -> None:
        closed_run_ids.append(request.run_id)

    session.__dict__["_close_run_scoped_llm_http_client"] = (
        _close_run_scoped_llm_http_client
    )

    with pytest.raises(RuntimeError, match="setup failed"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert closed_run_ids == ["run-1"]


@pytest.mark.asyncio
async def test_maybe_fallback_after_retry_exhausted_switches_profile() -> None:
    session = object.__new__(AgentLlmSession)
    primary_config = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    fallback_config = ModelEndpointConfig(
        model="fallback-model",
        base_url="https://fallback.test/v1",
        api_key="fallback-key",
        fallback_priority=10,
    )
    session.__dict__["_config"] = primary_config
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=1)

    class _FallbackMiddleware:
        def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
            return config.fallback_policy_id == "same_provider_then_other_provider"

        def select_fallback(self, **kwargs: object) -> LlmFallbackDecision:
            _ = kwargs
            return LlmFallbackDecision(
                policy_id="same_provider_then_other_provider",
                from_profile_name="primary",
                to_profile_name="secondary",
                from_provider=primary_config.provider,
                to_provider=fallback_config.provider,
                from_model=primary_config.model,
                to_model=fallback_config.model,
                hop=1,
                reason="rate_limited",
                cooldown_until=datetime.now(UTC),
                target_config=fallback_config,
            )

    activated: list[LlmFallbackDecision] = []
    session.__dict__["_fallback_middleware"] = _FallbackMiddleware()
    session.__dict__["_handle_fallback_activated"] = lambda **kwargs: activated.append(
        cast(LlmFallbackDecision, kwargs["decision"])
    )
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: None

    captured_generate_kwargs: list[dict[str, object]] = []

    class _FallbackSession:
        async def _generate_async(
            self,
            request: LLMRequest,
            **kwargs: object,
        ) -> str:
            _ = request
            captured_generate_kwargs.append(kwargs)
            return "fallback-response"

    session.__dict__["_clone_with_config"] = lambda **kwargs: _FallbackSession()

    result = await AgentLlmSession._maybe_fallback_after_retry_exhausted(
        session,
        request=_build_request(),
        retry_number=1,
        total_attempts=2,
        retry_error=LlmRetryErrorInfo(
            message="slow down",
            status_code=429,
            error_code="rate_limited",
            retryable=True,
            rate_limited=True,
        ),
        fallback_state=_FallbackAttemptState.initial("primary"),
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "fallback-response"
    assert result.status == _FallbackAttemptStatus.RECOVERED
    assert len(activated) == 1
    assert activated[0].to_profile_name == "secondary"
    assert captured_generate_kwargs[0]["retry_number"] == 0
    next_fallback_state = captured_generate_kwargs[0]["fallback_state"]
    assert getattr(next_fallback_state, "hop") == 1


@pytest.mark.asyncio
async def test_maybe_fallback_after_non_retryable_quota_error_switches_profile() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    primary_config = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    fallback_config = ModelEndpointConfig(
        model="fallback-model",
        base_url="https://fallback.test/v1",
        api_key="fallback-key",
        fallback_priority=10,
    )
    session.__dict__["_config"] = primary_config
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=3)

    class _FallbackMiddleware:
        def has_enabled_policy(self, config: ModelEndpointConfig) -> bool:
            return config.fallback_policy_id == "same_provider_then_other_provider"

        def select_fallback(self, **kwargs: object) -> LlmFallbackDecision:
            _ = kwargs
            return LlmFallbackDecision(
                policy_id="same_provider_then_other_provider",
                from_profile_name="primary",
                to_profile_name="secondary",
                from_provider=primary_config.provider,
                to_provider=fallback_config.provider,
                from_model=primary_config.model,
                to_model=fallback_config.model,
                hop=1,
                reason="insufficient_quota",
                cooldown_until=datetime.now(UTC),
                target_config=fallback_config,
            )

    session.__dict__["_fallback_middleware"] = _FallbackMiddleware()
    session.__dict__["_handle_fallback_activated"] = lambda **kwargs: None
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: None

    class _FallbackSession:
        async def _generate_async(
            self,
            request: LLMRequest,
            **kwargs: object,
        ) -> str:
            _ = (request, kwargs)
            return "fallback-response"

    session.__dict__["_clone_with_config"] = lambda **kwargs: _FallbackSession()

    result = await AgentLlmSession._maybe_fallback_after_retry_exhausted(
        session,
        request=_build_request(),
        retry_number=0,
        total_attempts=4,
        retry_error=LlmRetryErrorInfo(
            message="quota exceeded",
            status_code=400,
            error_code="insufficient_quota",
            retryable=False,
            rate_limited=True,
        ),
        fallback_state=_FallbackAttemptState.initial("primary"),
        attempt_text_emitted=False,
        attempt_tool_call_event_emitted=False,
        attempt_tool_outcome_event_emitted=False,
        attempt_messages_committed=False,
        skip_initial_user_prompt_persist=False,
    )

    assert result.response == "fallback-response"
    assert result.status == _FallbackAttemptStatus.RECOVERED


@pytest.mark.asyncio
async def test_generate_async_does_not_emit_retry_exhausted_after_fallback_exhausted() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session.__dict__["_config"] = ModelEndpointConfig(
        model="primary-model",
        base_url="https://example.test/v1",
        api_key="primary-key",
        fallback_policy_id="same_provider_then_other_provider",
    )
    session.__dict__["_profile_name"] = "primary"
    session.__dict__["_retry_config"] = LlmRetryConfig(max_retries=0)
    session.__dict__["_tool_registry"] = cast(object, None)
    session.__dict__["_skill_registry"] = cast(object, None)
    session.__dict__["_allowed_tools"] = ()
    session.__dict__["_allowed_mcp_servers"] = ()
    session.__dict__["_allowed_skills"] = ()
    session.__dict__["_task_repo"] = cast(object, None)
    session.__dict__["_shared_store"] = cast(object, None)
    session.__dict__["_event_bus"] = cast(object, None)
    session.__dict__["_message_repo"] = cast(
        MessageRepository, _FakeMessageRepo(history=[])
    )
    session.__dict__["_approval_ticket_repo"] = cast(object, None)
    session.__dict__["_run_runtime_repo"] = cast(object, None)
    session.__dict__["_injection_manager"] = type(
        "_InjectionManager",
        (),
        {"drain_at_boundary": lambda self, run_id, instance_id: []},
    )()
    session.__dict__["_run_event_hub"] = type(
        "_RunEventHub", (), {"publish": lambda self, event: None}
    )()
    session.__dict__["_agent_repo"] = cast(object, None)
    session.__dict__["_workspace_manager"] = type(
        "_WorkspaceManager",
        (),
        {"resolve": lambda self, **kwargs: cast(object, None)},
    )()
    session.__dict__["_role_memory_service"] = None
    session.__dict__["_media_asset_service"] = None
    session.__dict__["_computer_runtime"] = None
    session.__dict__["_background_task_service"] = None
    session.__dict__["_monitor_service"] = None
    session.__dict__["_metric_recorder"] = None
    session.__dict__["_token_usage_repo"] = None
    session.__dict__["_role_registry"] = cast(object, None)
    session.__dict__["_mcp_registry"] = McpRegistry()
    session.__dict__["_task_service"] = cast(object, None)
    session.__dict__["_task_execution_service"] = cast(object, object())
    session.__dict__["_tool_approval_manager"] = cast(object, None)
    session.__dict__["_shell_approval_repo"] = None
    session.__dict__["_notification_service"] = None
    session.__dict__["_im_tool_service"] = None
    session.__dict__["_resolve_tool_approval_policy"] = lambda run_id: cast(
        object, None
    )
    session.__dict__["_build_model_api_error_message"] = lambda error: "rate limited"
    session.__dict__["_persist_user_prompt_if_needed"] = lambda **kwargs: (
        kwargs["history"],
        False,
    )
    session.__dict__["_run_control_manager"] = type(
        "_RunControlManager",
        (),
        {
            "context": lambda self, run_id, instance_id: type(
                "_ControlContext",
                (),
                {"raise_if_cancelled": lambda self: None},
            )()
        },
    )()

    async def _no_recovery(**kwargs: object) -> None:
        _ = kwargs
        return None

    session.__dict__["_maybe_recover_from_tool_args_parse_failure"] = _no_recovery
    session.__dict__["_should_retry_request"] = lambda **kwargs: False
    session.__dict__["_fallback_middleware"] = type(
        "_FallbackMiddleware",
        (),
        {
            "has_enabled_policy": lambda self, config: True,
            "select_fallback": lambda self, **kwargs: None,
        },
    )()

    retry_exhausted_calls: list[dict[str, object]] = []
    fallback_exhausted_calls: list[dict[str, object]] = []
    retry_scheduled_calls: list[dict[str, object]] = []

    async def _capture_retry_scheduled(**kwargs: object) -> None:
        retry_scheduled_calls.append(kwargs)

    session.__dict__["_handle_retry_scheduled"] = _capture_retry_scheduled
    session.__dict__["_handle_retry_exhausted"] = lambda **kwargs: (
        retry_exhausted_calls.append(kwargs)
    )
    session.__dict__["_handle_fallback_exhausted"] = lambda **kwargs: (
        fallback_exhausted_calls.append(kwargs)
    )
    session.__dict__["_raise_assistant_run_error"] = lambda **kwargs: (
        _ for _ in ()
    ).throw(RuntimeError("stop after fallback exhaustion"))

    class _FailingAgentContext:
        async def __aenter__(self) -> object:
            request = httpx.Request("POST", "https://example.test/v1/chat/completions")
            response = httpx.Response(
                429,
                headers={"Retry-After": "1"},
                request=request,
            )
            raise APIStatusError(
                "rate limited",
                response=response,
                body={"error": {"code": "rate_limit_exceeded", "message": "slow down"}},
            )

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            _ = (exc_type, exc, tb)
            return False

    class _FailingAgent:
        def iter(self, *_args: object, **_kwargs: object) -> _FailingAgentContext:
            return _FailingAgentContext()

    async def _build_agent_iteration_context(
        **kwargs: object,
    ) -> tuple[str, list[object], str, object]:
        _ = kwargs
        return "", [], "System prompt", _FailingAgent()

    session.__dict__["_build_agent_iteration_context"] = _build_agent_iteration_context

    with pytest.raises(RuntimeError, match="stop after fallback exhaustion"):
        await AgentLlmSession._generate_async(
            session,
            _build_request(),
        )

    assert len(fallback_exhausted_calls) == 1
    assert retry_scheduled_calls == []
    assert retry_exhausted_calls == []
