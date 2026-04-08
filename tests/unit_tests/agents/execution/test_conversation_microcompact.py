# -*- coding: utf-8 -*-
from __future__ import annotations

from agent_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
)
from agent_teams.agents.execution.conversation_microcompact import (
    ConversationMicrocompactService,
)
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def test_microcompact_only_rewrites_old_tool_results() -> None:
    history: list[ModelRequest | ModelResponse] = [
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
                    content="A" * 5000,
                )
            ]
        ),
    ]
    history.extend(
        ModelRequest(parts=[UserPromptPart(content=f"tail-{index}")])
        for index in range(12)
    )
    service = ConversationMicrocompactService()

    result = service.apply(
        history=history,
        budget=ConversationCompactionBudget(
            context_window=400,
            history_trigger_tokens=50,
            history_target_tokens=10,
        ),
    )

    assert result.compacted_message_count == 1
    assert result.compacted_part_count == 1
    compacted_message = result.messages[1]
    assert isinstance(compacted_message, ModelRequest)
    compacted_part = compacted_message.parts[0]
    assert isinstance(compacted_part, ToolReturnPart)
    assert isinstance(compacted_part.content, str)
    assert compacted_part.content.startswith("[Compacted tool result]")
    assert "tool: read_file" in compacted_part.content
    assert result.messages[-1] == history[-1]
    assert result.estimated_tokens_after < result.estimated_tokens_before


def test_microcompact_keeps_open_tool_chains_intact() -> None:
    history: list[ModelRequest | ModelResponse] = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="read_file",
                    args='{"path":"README.md"}',
                    tool_call_id="call-1",
                )
            ]
        )
    ]
    history.extend(
        ModelRequest(parts=[UserPromptPart(content=f"tail-{index}" + ("x" * 200))])
        for index in range(13)
    )
    service = ConversationMicrocompactService()

    result = service.apply(
        history=history,
        budget=ConversationCompactionBudget(
            context_window=400,
            history_trigger_tokens=50,
            history_target_tokens=10,
        ),
    )

    assert result.compacted_message_count == 0
    assert result.compacted_part_count == 0
    assert list(result.messages) == history


def test_microcompact_is_deterministic_for_same_history() -> None:
    history: list[ModelRequest | ModelResponse] = [
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="grep",
                    args='{"pattern":"TODO"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="grep",
                    tool_call_id="call-1",
                    content="line\n" * 1200,
                )
            ]
        ),
    ]
    history.extend(
        ModelRequest(parts=[UserPromptPart(content=f"tail-{index}")])
        for index in range(12)
    )
    service = ConversationMicrocompactService()
    budget = ConversationCompactionBudget(
        context_window=400,
        history_trigger_tokens=50,
        history_target_tokens=10,
    )

    first = service.apply(history=history, budget=budget)
    second = service.apply(history=history, budget=budget)

    assert first == second


def test_microcompact_rewrites_tool_results_even_in_short_severe_history() -> None:
    history: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[UserPromptPart(content="seed")]),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="shell",
                    args='{"command":"printf x"}',
                    tool_call_id="call-1",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="shell",
                    tool_call_id="call-1",
                    content="payload-line\n" * 1400,
                )
            ]
        ),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    service = ConversationMicrocompactService()

    result = service.apply(
        history=history,
        budget=ConversationCompactionBudget(
            context_window=500,
            history_trigger_tokens=150,
            history_target_tokens=80,
        ),
    )

    assert result.compacted_message_count == 1
    assert result.compacted_part_count == 1
    compacted_message = result.messages[2]
    assert isinstance(compacted_message, ModelRequest)
    compacted_part = compacted_message.parts[0]
    assert isinstance(compacted_part, ToolReturnPart)
    assert isinstance(compacted_part.content, str)
    assert compacted_part.content.startswith("[Compacted tool result]")
