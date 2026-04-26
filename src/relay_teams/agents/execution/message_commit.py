# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from relay_teams.agents.execution.tool_call_history import (
    clone_model_request_with_parts,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import build_tool_error_result


class CommitMessageRepository(Protocol):
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
        pass

    def get_history_for_conversation(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError

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
        messages: list[ModelRequest | ModelResponse],
    ) -> None:
        pass

    async def get_history_for_conversation_async(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError


@runtime_checkable
class AsyncCommitMessageRepository(Protocol):
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
        messages: list[ModelRequest | ModelResponse],
    ) -> None:
        pass

    async def get_history_for_conversation_async(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError


class NormalizeCommittableMessages(Protocol):
    def __call__(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        raise NotImplementedError


class MessageCommitService:
    def __init__(
        self,
        *,
        message_repo: CommitMessageRepository,
    ) -> None:
        self._message_repo = message_repo

    async def _append_async(
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
        if isinstance(self._message_repo, AsyncCommitMessageRepository):
            await self._message_repo.append_async(
                session_id=session_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_role_id=agent_role_id,
                instance_id=instance_id,
                task_id=task_id,
                trace_id=trace_id,
                messages=messages,
            )
            return
        self._message_repo.append(
            session_id=session_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_role_id=agent_role_id,
            instance_id=instance_id,
            task_id=task_id,
            trace_id=trace_id,
            messages=messages,
        )

    async def _get_history_for_conversation_async(
        self,
        conversation_id: str,
    ) -> list[ModelRequest | ModelResponse]:
        if isinstance(self._message_repo, AsyncCommitMessageRepository):
            return await self._message_repo.get_history_for_conversation_async(
                conversation_id
            )
        return self._message_repo.get_history_for_conversation(conversation_id)

    def commit_ready_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        last_committable_index: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            int,
        ],
        has_tool_input_validation_failures: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            bool,
        ],
        normalize_committable_messages: NormalizeCommittableMessages,
        workspace_id: Callable[[LLMRequest], str],
        conversation_id: Callable[[LLMRequest], str],
        publish_committed_tool_outcome_events_from_messages: Callable[..., None],
        filter_model_messages: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            list[ModelRequest | ModelResponse],
        ],
        has_tool_side_effect_messages: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            bool,
        ],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        safe_index = last_committable_index(pending_messages)
        if safe_index <= 0:
            return history, pending_messages, False, False
        raw_ready = pending_messages[:safe_index]
        committed_tool_validation_failures = has_tool_input_validation_failures(
            raw_ready
        )
        ready = normalize_committable_messages(request=request, messages=raw_ready)
        resolved_conversation_id = conversation_id(request)
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=workspace_id(request),
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=ready,
        )
        publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=ready,
        )
        next_history = filter_model_messages(
            self._message_repo.get_history_for_conversation(resolved_conversation_id)
        )
        return (
            next_history,
            pending_messages[safe_index:],
            has_tool_side_effect_messages(ready),
            committed_tool_validation_failures,
        )

    async def commit_ready_messages_async(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        last_committable_index: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            int,
        ],
        has_tool_input_validation_failures: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            bool,
        ],
        normalize_committable_messages: NormalizeCommittableMessages,
        workspace_id: Callable[[LLMRequest], str],
        conversation_id: Callable[[LLMRequest], str],
        publish_committed_tool_outcome_events_from_messages: Callable[
            ...,
            Awaitable[None],
        ],
        filter_model_messages: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            list[ModelRequest | ModelResponse],
        ],
        has_tool_side_effect_messages: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            bool,
        ],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        _ = history
        safe_index = last_committable_index(pending_messages)
        if safe_index <= 0:
            return history, pending_messages, False, False
        raw_ready = pending_messages[:safe_index]
        committed_tool_validation_failures = has_tool_input_validation_failures(
            raw_ready
        )
        ready = normalize_committable_messages(request=request, messages=raw_ready)
        resolved_conversation_id = conversation_id(request)
        await self._append_async(
            session_id=request.session_id,
            workspace_id=workspace_id(request),
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=ready,
        )
        await publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=ready,
        )
        next_history = filter_model_messages(
            await self._get_history_for_conversation_async(resolved_conversation_id)
        )
        return (
            next_history,
            pending_messages[safe_index:],
            has_tool_side_effect_messages(ready),
            committed_tool_validation_failures,
        )

    def commit_all_safe_messages(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        commit_ready_messages: Callable[
            ...,
            tuple[
                list[ModelRequest | ModelResponse],
                list[ModelRequest | ModelResponse],
                bool,
                bool,
            ],
        ],
        last_committable_index: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            int,
        ],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        next_history = history
        remaining = list(pending_messages)
        tool_events_published = False
        tool_validation_failures_committed = False
        while remaining:
            safe_index = last_committable_index(remaining)
            if safe_index <= 0:
                break
            (
                next_history,
                remaining,
                committed_tool_events_published,
                committed_tool_validation_failures,
            ) = commit_ready_messages(
                request=request,
                history=next_history,
                pending_messages=remaining,
            )
            if committed_tool_events_published:
                tool_events_published = True
            if committed_tool_validation_failures:
                tool_validation_failures_committed = True
        return (
            next_history,
            remaining,
            tool_events_published,
            tool_validation_failures_committed,
        )

    # noinspection PyMethodMayBeStatic
    async def commit_all_safe_messages_async(
        self,
        *,
        request: LLMRequest,
        history: list[ModelRequest | ModelResponse],
        pending_messages: list[ModelRequest | ModelResponse],
        commit_ready_messages: Callable[
            ...,
            Awaitable[
                tuple[
                    list[ModelRequest | ModelResponse],
                    list[ModelRequest | ModelResponse],
                    bool,
                    bool,
                ]
            ],
        ],
        last_committable_index: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            int,
        ],
    ) -> tuple[
        list[ModelRequest | ModelResponse],
        list[ModelRequest | ModelResponse],
        bool,
        bool,
    ]:
        next_history = history
        remaining = list(pending_messages)
        tool_events_published = False
        tool_validation_failures_committed = False
        while remaining:
            safe_index = last_committable_index(remaining)
            if safe_index <= 0:
                break
            (
                next_history,
                remaining,
                committed_tool_events_published,
                committed_tool_validation_failures,
            ) = await commit_ready_messages(
                request=request,
                history=next_history,
                pending_messages=remaining,
            )
            if committed_tool_events_published:
                tool_events_published = True
            if committed_tool_validation_failures:
                tool_validation_failures_committed = True
        return (
            next_history,
            remaining,
            tool_events_published,
            tool_validation_failures_committed,
        )

    def has_pending_tool_calls(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
        *,
        last_committable_index: Callable[
            [Sequence[ModelRequest | ModelResponse]],
            int,
        ],
    ) -> bool:
        return last_committable_index(messages) < len(messages)

    def has_tool_input_validation_failures(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> bool:
        for message in messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    return True
        return False

    def normalize_committable_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> list[ModelRequest | ModelResponse]:
        _ = request
        normalized: list[ModelRequest | ModelResponse] = []
        for message in messages:
            if isinstance(message, ModelResponse):
                normalized.append(message)
                continue
            next_parts: list[ModelRequestPart] = []
            changed = False
            for part in message.parts:
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    next_parts.append(
                        ToolReturnPart(
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            content=build_tool_error_result(
                                error_code="tool_input_validation_failed",
                                message=str(part.content or "").strip()
                                or "Tool input validation failed.",
                            ),
                        )
                    )
                    changed = True
                    continue
                next_parts.append(part)
            if changed:
                normalized.append(clone_model_request_with_parts(message, next_parts))
                continue
            normalized.append(message)
        return normalized

    def last_committable_index(
        self,
        messages: Sequence[ModelRequest | ModelResponse],
    ) -> int:
        pending: set[str] = set()
        last_safe_index = 0
        for index, msg in enumerate(messages, start=1):
            if isinstance(msg, ModelResponse):
                for part in msg.parts:
                    if not isinstance(part, ToolCallPart):
                        continue
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if tool_call_id:
                        pending.add(tool_call_id)
            else:
                for part in msg.parts:
                    tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
                    if not tool_call_id:
                        continue
                    if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                        pending.discard(tool_call_id)
            if not pending:
                last_safe_index = index
        return last_safe_index
