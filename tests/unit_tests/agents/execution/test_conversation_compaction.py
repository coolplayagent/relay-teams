# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import Sequence

import pytest

import agent_teams.agents.execution.conversation_compaction as compaction_module
from agent_teams.agents.execution.conversation_compaction import (
    ConversationCompactionBudget,
    ConversationCompactionPlan,
    ConversationCompactionService,
    ConversationCompactionStrategy,
    DefaultConversationCompactionStrategy,
)
from agent_teams.agents.execution.message_repository import MessageRepository
from agent_teams.providers.model_config import LlmRetryConfig, ModelEndpointConfig
from agent_teams.sessions.session_history_marker_models import SessionHistoryMarkerType
from agent_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)
from agent_teams.workspace import build_conversation_id
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
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

    def __init__(self, *args: object, **kwargs: object) -> None:
        _ = (args, kwargs)

    @asynccontextmanager
    async def iter(self, prompt: str) -> AsyncIterator[_FakeAgentRun]:
        _ = prompt
        yield _FakeAgentRun()


class _FakeAgentRun:
    def __init__(self) -> None:
        self.ctx = object()
        self.result = type(
            "_Result", (), {"output": "## Active summary\n- remember this"}
        )()
        self._nodes = [_FakeModelRequestNode()]

    def __aiter__(self) -> _FakeAgentRun:
        return self

    async def __anext__(self) -> _FakeModelRequestNode:
        if not self._nodes:
            raise StopAsyncIteration
        return self._nodes.pop(0)


class _FakeStream:
    def __aiter__(self) -> _FakeStream:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


class _FakeModelRequestNode:
    @asynccontextmanager
    async def stream(self, _ctx: object) -> AsyncIterator[_FakeStream]:
        yield _FakeStream()


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
