# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolCallPartDelta,
    ToolReturnPart,
)

from relay_teams.agents.execution.stream_events import StreamEventService
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.assistant_errors import build_tool_error_result

LOGGER = get_logger(__name__)


class ToolArgsRecoveryMessageRepository(Protocol):
    def prune_conversation_history_to_safe_boundary(
        self,
        conversation_id: str,
    ) -> None: ...

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
    ) -> None: ...


class ToolArgsRecoveryService:
    def __init__(
        self,
        *,
        message_repo: ToolArgsRecoveryMessageRepository,
        stream_event_service: StreamEventService,
    ) -> None:
        self._message_repo = message_repo
        self._stream_event_service = stream_event_service

    async def maybe_recover_from_tool_args_parse_failure(
        self,
        *,
        request: LLMRequest,
        retry_number: int,
        total_attempts: int,
        emitted_text_chunks: list[str],
        published_tool_call_ids: set[str],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        error_message: str,
        conversation_id: Callable[[LLMRequest], str],
        workspace_id: Callable[[LLMRequest], str],
        publish_tool_call_events_from_messages: Callable[..., object],
        publish_committed_tool_outcome_events_from_messages: Callable[..., object],
        raise_assistant_run_error: Callable[..., None],
        generate_async: Callable[..., Awaitable[str]],
    ) -> str | None:
        if not self._stream_event_service.looks_like_tool_args_parse_failure(
            error_message
        ):
            return None
        salvageable_calls = (
            self._stream_event_service.collect_salvageable_stream_tool_calls(
                streamed_tool_calls
            )
        )
        if not salvageable_calls:
            return None

        response_parts: list[TextPart | ToolCallPart] = []
        partial_text = "".join(emitted_text_chunks).strip()
        if partial_text:
            response_parts.append(TextPart(content=partial_text))
        response_parts.extend(salvageable_calls)
        assistant_response = ModelResponse(parts=response_parts)
        tool_error_parts = [
            ToolReturnPart(
                tool_name=tool_call.tool_name,
                tool_call_id=tool_call.tool_call_id,
                content=build_tool_error_result(
                    error_code="tool_input_validation_failed",
                    message=(
                        "Tool arguments were not valid JSON. "
                        "The provider rejected the malformed tool call before execution. "
                        f"Details: {error_message}"
                    ),
                ),
            )
            for tool_call in salvageable_calls
        ]
        tool_error_request = ModelRequest(parts=tool_error_parts)
        resolved_conversation_id = conversation_id(request)
        self._message_repo.prune_conversation_history_to_safe_boundary(
            resolved_conversation_id
        )
        self._message_repo.append(
            session_id=request.session_id,
            workspace_id=workspace_id(request),
            conversation_id=resolved_conversation_id,
            agent_role_id=request.role_id,
            instance_id=request.instance_id,
            task_id=request.task_id,
            trace_id=request.trace_id,
            messages=[assistant_response, tool_error_request],
        )
        publish_tool_call_events_from_messages(
            request=request,
            messages=[assistant_response],
            published_tool_call_ids=published_tool_call_ids,
        )
        publish_committed_tool_outcome_events_from_messages(
            request=request,
            messages=[tool_error_request],
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.tool_args_parse_failure.recovered",
            message="Recovered from malformed tool arguments by emitting an error tool result",
            payload={
                "role_id": request.role_id,
                "instance_id": request.instance_id,
                "tool_call_ids": [
                    str(tool_call.tool_call_id or "") for tool_call in salvageable_calls
                ],
            },
        )
        next_retry_number = retry_number + 1
        if next_retry_number >= total_attempts:
            log_event(
                LOGGER,
                logging.ERROR,
                event="llm.tool_args_parse_failure.recovery_exhausted",
                message=(
                    "Malformed tool argument recovery budget exhausted; failing request"
                ),
                payload={
                    "role_id": request.role_id,
                    "instance_id": request.instance_id,
                    "retry_number": retry_number,
                    "total_attempts": total_attempts,
                },
            )
            raise_assistant_run_error(
                request=request,
                error_code="model_tool_args_invalid_json",
                error_message=error_message,
            )
        return await generate_async(
            request,
            retry_number=next_retry_number,
            total_attempts=total_attempts,
            skip_initial_user_prompt_persist=True,
        )
