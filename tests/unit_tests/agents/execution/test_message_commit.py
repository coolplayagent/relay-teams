# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Sequence

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.messages import ToolReturnPart

from relay_teams.agents.execution.message_commit import MessageCommitService
from relay_teams.providers.provider_contracts import LLMRequest

from .agent_llm_session_test_support import _build_request


class _CommitRepo:
    def __init__(self) -> None:
        self.appended: list[ModelRequest | ModelResponse] = []
        self.history_read_count = 0

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
        messages: Sequence[ModelRequest | ModelResponse],
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
        self.appended.extend(messages)

    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        _ = conversation_id
        self.history_read_count += 1
        return []

    async def append_async(
        self,
        *,
        session_id: str,
        workspace_id: str,
        conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> None:
        self.append(
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=agent_role_id,
            instance_id=instance_id,
            task_id=task_id,
            trace_id=trace_id,
            messages=messages,
        )

    async def get_history_for_conversation_async(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        return self.get_history_for_conversation(conversation_id)


@pytest.mark.asyncio
async def test_commit_ready_messages_async_uses_in_memory_history_by_default() -> None:
    repo = _CommitRepo()
    service = MessageCommitService(message_repo=repo)
    published_messages: list[tuple[ModelRequest | ModelResponse, ...]] = []

    async def _publish_outcomes(
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        _ = (request, published_tool_outcome_ids)
        published_messages.append(tuple(messages))
        return True

    response = ModelResponse(
        parts=[
            ToolCallPart(
                tool_name="read",
                args='{"path":"README.md"}',
                tool_call_id="call-read",
            )
        ]
    )
    result = ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name="read",
                content={"ok": True},
                tool_call_id="call-read",
            )
        ]
    )

    (
        next_history,
        remaining,
        tool_events_published,
        validation_failures,
    ) = await service.commit_ready_messages_async(
        request=_build_request(),
        history=[],
        pending_messages=[response, result],
        last_committable_index=len,
        has_tool_input_validation_failures=lambda messages: False,
        normalize_committable_messages=_normalize_messages,
        workspace_id=lambda request: request.workspace_id,
        conversation_id=lambda request: request.conversation_id,
        publish_committed_tool_outcome_events_from_messages=_publish_outcomes,
        filter_model_messages=list,
        has_tool_side_effect_messages=lambda messages: True,
    )

    assert remaining == []
    assert next_history == [response, result]
    assert repo.appended == [response, result]
    assert repo.history_read_count == 0
    assert published_messages == [(response, result)]
    assert tool_events_published is True
    assert validation_failures is False


def _normalize_messages(
    *,
    request: LLMRequest,
    messages: Sequence[ModelRequest | ModelResponse],
) -> list[ModelRequest | ModelResponse]:
    _ = request
    return list(messages)
