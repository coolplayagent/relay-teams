# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast

import pytest

from agent_teams.agents.execution.llm_session import AgentLlmSession
from agent_teams.agents.execution.conversation_compaction import (
    ConversationCompactionService,
)
from agent_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
    ConversationMicrocompactResult,
)
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.mcp.mcp_models import McpConfigScope, McpServerSpec
from agent_teams.mcp.mcp_registry import McpRegistry
from agent_teams.providers.model_config import ModelEndpointConfig
from agent_teams.providers.provider_contracts import LLMRequest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
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

    def get_history_for_conversation(
        self,
        _conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        return list(self._history)


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
    assert compaction_call["estimated_tokens_before_microcompact"] == 260
    assert compaction_call["estimated_tokens_after_microcompact"] == 80


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
