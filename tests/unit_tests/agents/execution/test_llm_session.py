# -*- coding: utf-8 -*-
from __future__ import annotations

import json

import httpx
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from openai import APIStatusError

from relay_teams.agents.execution.llm_session import AgentLlmSession
from relay_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from relay_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
    ConversationMicrocompactResult,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.providers.llm_retry import LlmRetrySchedule
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.run_intent_repo import RunIntentRepository
from relay_teams.sessions.runs.assistant_errors import AssistantRunError
from relay_teams.hooks import HookDecisionBundle, HookDecisionType, HookEventName
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
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
        workspace_id: str,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> bool:
        _ = (
            session_id,
            instance_id,
            task_id,
            trace_id,
            content,
            workspace_id,
            conversation_id,
            agent_role_id,
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

    assert message_repo.pruned_conversation_ids == ["conv-1"]
    assert len(message_repo.append_calls) == 1
    assert next_history[:-1] == compacted_history
    assert rebuild_context is False
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
