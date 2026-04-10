# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

import pytest

import relay_teams.agents.execution.conversation_compaction as compaction_module
from relay_teams.agents.execution.conversation_compaction import (
    build_conversation_compaction_budget,
    ConversationCompactionBudget,
    ConversationCompactionPlan,
    ConversationCompactionService,
    ConversationCompactionStrategy,
    DefaultConversationCompactionStrategy,
)
from relay_teams.agents.execution.message_repository import MessageRepository
from relay_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from relay_teams.sessions.session_history_marker_models import SessionHistoryMarkerType
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from relay_teams.workspace import build_conversation_id
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


class _FixedStrategy(ConversationCompactionStrategy):
    def __init__(self, plan: ConversationCompactionPlan) -> None:
        self._plan = plan

    def plan(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        budget: ConversationCompactionBudget,
    ) -> ConversationCompactionPlan:
        _ = (history, budget)
        return self._plan


class _FakeAgent:
    def __class_getitem__(cls, _item: object) -> type[_FakeAgent]:
        return cls

    def __init__(
        self,
        *args: object,
        output: str = "## Active summary\n- remember this",
        stream_events: Sequence[object] = (),
        **kwargs: object,
    ) -> None:
        _ = (args, kwargs)
        self._output = output
        self._stream_events = tuple(stream_events)

    @asynccontextmanager
    async def iter(self, prompt: str) -> AsyncIterator[_FakeAgentRun]:
        _ = prompt
        yield _FakeAgentRun(output=self._output, stream_events=self._stream_events)


class _FakeAgentRun:
    def __init__(
        self,
        *,
        output: str = "## Active summary\n- remember this",
        stream_events: Sequence[object] = (),
    ) -> None:
        self.ctx = object()
        self.result = type("_Result", (), {"output": output})()
        self._nodes = [_FakeModelRequestNode(stream_events=stream_events)]

    def __aiter__(self) -> _FakeAgentRun:
        return self

    async def __anext__(self) -> _FakeModelRequestNode:
        if not self._nodes:
            raise StopAsyncIteration
        return self._nodes.pop(0)


class _FakeStream:
    def __init__(self, events: Sequence[object] = ()) -> None:
        self._events = list(events)

    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> object:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class _FakeModelRequestNode:
    def __init__(self, *, stream_events: Sequence[object] = ()) -> None:
        self._stream_events = tuple(stream_events)

    @asynccontextmanager
    async def stream(self, _ctx: object) -> AsyncIterator[_FakeStream]:
        yield _FakeStream(self._stream_events)


def test_default_conversation_compaction_strategy_respects_threshold_and_tail() -> None:
    strategy = DefaultConversationCompactionStrategy()
    history = [
        ModelRequest(parts=[UserPromptPart(content=f"turn-{index}-" + ("x" * 120))])
        for index in range(20)
    ]

    budget = ConversationCompactionBudget(
        context_window=400,
        history_trigger_tokens=320,
        history_target_tokens=200,
    )

    plan = strategy.plan(history=history, budget=budget)

    assert plan.should_compact is True
    assert plan.compacted_message_count > 0
    assert plan.protected_tail_messages == 5
    assert plan.kept_message_count == 5
    assert plan.estimated_tokens_before >= plan.threshold_tokens


def test_compaction_budget_keeps_positive_history_budget_under_high_pressure() -> None:
    budget = build_conversation_compaction_budget(
        context_window=100,
        estimated_system_prompt_tokens=45,
        estimated_user_prompt_tokens=20,
        estimated_tool_context_tokens=20,
        estimated_output_reserve_tokens=15,
    )
    strategy = DefaultConversationCompactionStrategy()
    history = [
        ModelRequest(parts=[UserPromptPart(content="turn-" + ("x" * 200))]),
        ModelResponse(parts=[TextPart(content="reply-" + ("y" * 200))]),
    ]

    plan = strategy.plan(history=history, budget=budget)

    assert budget.history_trigger_tokens == 1
    assert budget.history_target_tokens == 1
    assert plan.should_compact is False


def test_default_conversation_compaction_strategy_requires_replayable_suffix() -> None:
    strategy = DefaultConversationCompactionStrategy()
    history = [
        ModelRequest(parts=[UserPromptPart(content="inspect the file")]),
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
                    content="file contents",
                )
            ]
        ),
    ]

    plan = strategy.plan(
        history=history,
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=1,
            history_target_tokens=1,
        ),
    )

    assert plan.should_compact is False
    assert plan.compacted_message_count == 0
    assert plan.kept_message_count == len(history)


def test_default_conversation_compaction_strategy_precomputes_replayable_suffixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = DefaultConversationCompactionStrategy()
    history = [
        ModelRequest(parts=[UserPromptPart(content=f"turn-{index}-" + ("x" * 120))])
        for index in range(20)
    ]

    monkeypatch.setattr(
        compaction_module,
        "is_replayable_history",
        lambda _history: (_ for _ in ()).throw(
            AssertionError("plan() should not rescan suffix replayability")
        ),
    )

    plan = strategy.plan(
        history=history,
        budget=ConversationCompactionBudget(
            context_window=400,
            history_trigger_tokens=320,
            history_target_tokens=200,
        ),
    )

    assert plan.should_compact is True
    assert plan.compacted_message_count > 0


def test_render_transcript_does_not_clip_mid_line_fact() -> None:
    history = [
        ModelRequest(parts=[UserPromptPart(content="preserve the exact facts")]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="shell",
                    tool_call_id="call-1",
                    content=(
                        "line-a\n"
                        "- phase-4 anchor: lunar-mint-407\n"
                        "- phase-4 checksum: CHK-P4-DQ7"
                    ),
                )
            ]
        ),
    ]

    transcript = compaction_module._render_transcript(history, max_chars=80)

    assert "lunar-min" not in transcript
    assert "phase-4 anchor" not in transcript
    assert transcript.splitlines()[-1] == "Tool result [shell]: line-a"


def test_render_transcript_keeps_single_line_message_body_when_truncated() -> None:
    history = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="preserve the exact facts about lunar-mint-407",
                )
            ]
        )
    ]

    transcript = compaction_module._render_transcript(history, max_chars=32)

    assert transcript.startswith("User/Tool\nUser:")
    assert "User/Tool\nUser: p" in transcript


def test_render_transcript_does_not_clip_first_tool_line_mid_token() -> None:
    history = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="shell",
                    tool_call_id="call-1",
                    content=(
                        "ROLLING-SUMMARY | phase-4 anchor=lunar-mint-407 | "
                        "phase-4 checksum=CHK-P4-DQ7\n"
                        "tail line"
                    ),
                )
            ]
        )
    ]

    transcript = compaction_module._render_transcript(history, max_chars=72)

    assert "lunar-min" not in transcript
    assert transcript.startswith("User/Tool\nTool result [shell]:")


@pytest.mark.asyncio
async def test_conversation_compaction_service_hides_messages_and_creates_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversation_compaction.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")
    for index in range(4):
        message_repo.append(
            session_id="session-1",
            workspace_id="default",
            conversation_id=conversation_id,
            agent_role_id="writer",
            instance_id="inst-1",
            task_id=f"task-{index + 1}",
            trace_id="run-1",
            messages=[
                ModelRequest(parts=[UserPromptPart(content=f"turn-{index + 1}")]),
            ],
        )

    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=message_repo,
        session_history_marker_repo=marker_repo,
        strategy=_FixedStrategy(
            ConversationCompactionPlan(
                should_compact=True,
                estimated_tokens_before=90,
                estimated_tokens_after=50,
                threshold_tokens=80,
                target_tokens=50,
                compacted_message_count=2,
                kept_message_count=2,
                protected_tail_messages=6,
                source_char_budget=12000,
            )
        ),
    )
    monkeypatch.setattr(compaction_module, "Agent", _FakeAgent)
    monkeypatch.setattr(compaction_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(service, "_build_model", lambda: object())

    next_history = await service.maybe_compact(
        session_id="session-1",
        role_id="writer",
        conversation_id=conversation_id,
        history=message_repo.get_history_for_conversation(conversation_id),
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=80,
            history_target_tokens=50,
        ),
        estimated_tokens_before_microcompact=120,
        estimated_tokens_after_microcompact=84,
    )

    assert len(next_history) == 2
    latest_marker = marker_repo.get_latest(
        "session-1",
        marker_type=SessionHistoryMarkerType.COMPACTION,
    )
    assert latest_marker is not None
    assert latest_marker.metadata["conversation_id"] == conversation_id
    assert latest_marker.metadata["compaction_strategy"] == "rolling_summary"
    assert latest_marker.metadata["estimated_tokens_before"] == "120"
    assert latest_marker.metadata["estimated_tokens_after_microcompact"] == "84"
    assert latest_marker.metadata["estimated_tokens_after_compact"] == "50"
    assert latest_marker.metadata["kept_message_count"] == "2"
    assert latest_marker.metadata["protected_tail_messages"] == "6"
    assert (
        latest_marker.metadata["summary_markdown"]
        == "## Active summary\n- remember this"
    )
    raw_messages = message_repo.get_messages_by_session(
        "session-1",
        include_cleared=True,
        include_hidden_from_context=True,
    )
    hidden_messages = [
        message for message in raw_messages if message["hidden_from_context"]
    ]
    assert len(hidden_messages) == 2
    assert (
        service.get_latest_summary(
            session_id="session-1",
            conversation_id=conversation_id,
        )
        == "## Active summary\n- remember this"
    )


@pytest.mark.asyncio
async def test_run_streaming_summary_prefers_streamed_text_when_result_is_truncated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "conversation_compaction_streaming_summary.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=MessageRepository(
            db_path,
            session_history_marker_repo=marker_repo,
        ),
        session_history_marker_repo=marker_repo,
    )
    agent = _FakeAgent(
        output="## Active summary\n- lunar-min",
        stream_events=(
            PartStartEvent(
                index=0,
                part=TextPart(content="## Active summary\n- lunar-min"),
            ),
            PartDeltaEvent(
                index=0,
                delta=TextPartDelta(content_delta="t-407"),
            ),
        ),
    )
    monkeypatch.setattr(compaction_module, "ModelRequestNode", _FakeModelRequestNode)

    summary = await service._run_streaming_summary(
        agent=cast(compaction_module.Agent[None, str], agent),
        prompt="Rewrite the summary.",
    )

    assert summary == "## Active summary\n- lunar-mint-407"


@pytest.mark.asyncio
async def test_conversation_compaction_service_preserves_microcompacted_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversation_compaction_suffix.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=[
            ModelRequest(parts=[UserPromptPart(content="turn-0")]),
            ModelRequest(parts=[UserPromptPart(content="turn-1")]),
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
                        content="original payload " * 800,
                    )
                ]
            ),
        ],
    )

    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=message_repo,
        session_history_marker_repo=marker_repo,
        strategy=_FixedStrategy(
            ConversationCompactionPlan(
                should_compact=True,
                estimated_tokens_before=120,
                estimated_tokens_after=40,
                threshold_tokens=80,
                target_tokens=40,
                compacted_message_count=1,
                kept_message_count=3,
                protected_tail_messages=3,
                source_char_budget=12000,
            )
        ),
    )
    monkeypatch.setattr(compaction_module, "Agent", _FakeAgent)
    monkeypatch.setattr(compaction_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(service, "_build_model", lambda: object())

    next_history = await service.maybe_compact(
        session_id="session-1",
        role_id="writer",
        conversation_id=conversation_id,
        history=[
            ModelRequest(parts=[UserPromptPart(content="turn-0")]),
            ModelRequest(parts=[UserPromptPart(content="turn-1")]),
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
                        content="[Compacted tool result]\ntool: read_file",
                    )
                ]
            ),
        ],
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=80,
            history_target_tokens=40,
        ),
    )

    assert len(next_history) == 3
    kept_user_message = next_history[0]
    assert isinstance(kept_user_message, ModelRequest)
    kept_user_part = kept_user_message.parts[0]
    assert isinstance(kept_user_part, UserPromptPart)
    compacted_message = next_history[-1]
    assert isinstance(compacted_message, ModelRequest)
    compacted_part = compacted_message.parts[0]
    assert isinstance(compacted_part, ToolReturnPart)
    assert compacted_part.content == "[Compacted tool result]\ntool: read_file"


@pytest.mark.asyncio
async def test_conversation_compaction_service_summarizes_original_history_before_microcompact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversation_compaction_source_history.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")
    original_history = [
        ModelRequest(parts=[UserPromptPart(content="inspect the file")]),
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
                    content="original payload " * 800,
                )
            ]
        ),
        ModelRequest(parts=[UserPromptPart(content="continue from the analysis")]),
    ]
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=original_history,
    )
    live_history = [
        original_history[0],
        original_history[1],
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="read_file",
                    tool_call_id="call-1",
                    content="[Compacted tool result]\ntool: read_file",
                )
            ]
        ),
        original_history[3],
    ]
    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=message_repo,
        session_history_marker_repo=marker_repo,
        strategy=_FixedStrategy(
            ConversationCompactionPlan(
                should_compact=True,
                estimated_tokens_before=160,
                estimated_tokens_after=40,
                threshold_tokens=80,
                target_tokens=40,
                compacted_message_count=3,
                kept_message_count=1,
                protected_tail_messages=1,
                source_char_budget=12000,
            )
        ),
    )
    captured_source_history: list[ModelRequest | ModelResponse] = []

    async def _fake_rewrite_summary(
        *,
        role_id: str,
        existing_summary: str,
        source_history: Sequence[ModelRequest | ModelResponse],
        source_char_budget: int,
    ) -> str:
        _ = (role_id, existing_summary, source_char_budget)
        captured_source_history.extend(source_history)
        return "## Active summary\n- preserve the original source"

    monkeypatch.setattr(service, "_rewrite_summary", _fake_rewrite_summary)
    next_history = await service.maybe_compact(
        session_id="session-1",
        role_id="writer",
        conversation_id=conversation_id,
        history=live_history,
        source_history=original_history,
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=80,
            history_target_tokens=40,
        ),
    )

    assert next_history == [original_history[3]]
    assert len(captured_source_history) == 3
    captured_message = captured_source_history[-1]
    assert isinstance(captured_message, ModelRequest)
    captured_part = captured_message.parts[0]
    assert isinstance(captured_part, ToolReturnPart)
    assert str(captured_part.content).startswith("original payload")


@pytest.mark.asyncio
async def test_conversation_compaction_service_skips_invalid_nonreplayable_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversation_compaction_invalid_suffix.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    message_repo = MessageRepository(
        db_path,
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")
    history = [
        ModelRequest(parts=[UserPromptPart(content="inspect the file")]),
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
                    content="file contents",
                )
            ]
        ),
    ]
    message_repo.append(
        session_id="session-1",
        workspace_id="default",
        conversation_id=conversation_id,
        agent_role_id="writer",
        instance_id="inst-1",
        task_id="task-1",
        trace_id="run-1",
        messages=history,
    )

    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=message_repo,
        session_history_marker_repo=marker_repo,
        strategy=_FixedStrategy(
            ConversationCompactionPlan(
                should_compact=True,
                estimated_tokens_before=120,
                estimated_tokens_after=40,
                threshold_tokens=80,
                target_tokens=40,
                compacted_message_count=1,
                kept_message_count=2,
                protected_tail_messages=2,
                source_char_budget=12000,
            )
        ),
    )
    monkeypatch.setattr(compaction_module, "Agent", _FakeAgent)
    monkeypatch.setattr(compaction_module, "ModelRequestNode", _FakeModelRequestNode)
    monkeypatch.setattr(service, "_build_model", lambda: object())

    next_history = await service.maybe_compact(
        session_id="session-1",
        role_id="writer",
        conversation_id=conversation_id,
        history=history,
        budget=ConversationCompactionBudget(
            context_window=100,
            history_trigger_tokens=80,
            history_target_tokens=40,
        ),
    )

    assert next_history == history
    latest_marker = marker_repo.get_latest(
        "session-1",
        marker_type=SessionHistoryMarkerType.COMPACTION,
    )
    assert latest_marker is None


def test_compaction_prompt_section_ignores_summaries_before_latest_clear(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "conversation_compaction_prompt.db"
    marker_repo = SessionHistoryMarkerRepository(db_path)
    service = ConversationCompactionService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=100,
        ),
        retry_config=LlmRetryConfig(),
        message_repo=MessageRepository(
            db_path, session_history_marker_repo=marker_repo
        ),
        session_history_marker_repo=marker_repo,
    )
    conversation_id = build_conversation_id("session-1", "writer")

    _ = marker_repo.create(
        session_id="session-1",
        marker_type=SessionHistoryMarkerType.COMPACTION,
        metadata={
            "conversation_id": conversation_id,
            "role_id": "writer",
            "summary_markdown": "old summary",
        },
    )
    _ = marker_repo.create_clear_marker("session-1")

    assert (
        service.build_prompt_section(
            session_id="session-1",
            conversation_id=conversation_id,
        )
        == ""
    )


def test_default_conversation_compaction_strategy_compacts_short_history_when_severe() -> (
    None
):
    strategy = DefaultConversationCompactionStrategy()
    history = [
        ModelRequest(parts=[UserPromptPart(content="seed")]),
        ModelResponse(parts=[TextPart(content="tool calls echoed " + ("x" * 600))]),
        ModelRequest(parts=[UserPromptPart(content="tool results " + ("y" * 5000))]),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    budget = ConversationCompactionBudget(
        context_window=600,
        history_trigger_tokens=200,
        history_target_tokens=100,
    )

    plan = strategy.plan(history=history, budget=budget)

    assert plan.should_compact is True
    assert plan.protected_tail_messages == 1
    assert plan.compacted_message_count >= 1
