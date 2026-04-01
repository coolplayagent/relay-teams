# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Sequence

from openai.types import chat
from openai.types.chat.chat_completion_message_function_tool_call_param import (
    ChatCompletionMessageFunctionToolCallParam,
)
from pydantic_ai._utils import guard_tool_call_id
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel

from agent_teams.logger import get_logger, log_event
from agent_teams.agents.execution.tool_args_repair import repair_tool_args

LOGGER = get_logger(__name__)


class RecoverableOpenAIChatModel(OpenAIChatModel):
    """OpenAI chat model that sanitizes malformed historical tool args on replay."""

    async def _map_messages(
        self,
        messages: Sequence[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> list[chat.ChatCompletionMessageParam]:
        return await super()._map_messages(
            self._sanitize_replayed_messages(messages),
            model_request_parameters,
        )

    @staticmethod
    def _map_tool_call(t: ToolCallPart) -> ChatCompletionMessageFunctionToolCallParam:
        repaired = repair_tool_args(t.args)
        if repaired.repair_applied or repaired.fallback_invalid_json:
            log_event(
                LOGGER,
                logging.WARNING,
                event="llm.tool_call_args.sanitized_for_replay",
                message="Sanitized malformed tool call arguments before replaying history",
                payload={
                    "tool_name": t.tool_name,
                    "tool_call_id": str(t.tool_call_id or ""),
                    "repair_applied": repaired.repair_applied,
                    "repair_succeeded": repaired.repair_succeeded,
                    "fallback_invalid_json": repaired.fallback_invalid_json,
                },
            )
        return ChatCompletionMessageFunctionToolCallParam(
            id=guard_tool_call_id(t=t),
            type="function",
            function={"name": t.tool_name, "arguments": repaired.arguments_json},
        )

    @classmethod
    def _sanitize_replayed_messages(
        cls,
        messages: Sequence[ModelMessage],
    ) -> list[ModelMessage]:
        sanitized_messages: list[ModelMessage] = []
        seen_tool_call_ids: set[str] = set()
        for message in messages:
            if isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ToolCallPart):
                        tool_call_id = str(part.tool_call_id or "").strip()
                        if tool_call_id:
                            seen_tool_call_ids.add(tool_call_id)
                sanitized_messages.append(message)
                continue
            if isinstance(message, ModelRequest):
                next_parts = cls._sanitize_request_parts(
                    parts=message.parts,
                    seen_tool_call_ids=seen_tool_call_ids,
                )
                if next_parts:
                    sanitized_messages.append(ModelRequest(parts=next_parts))
                continue
            sanitized_messages.append(message)
        return sanitized_messages

    @staticmethod
    def _sanitize_request_parts(
        *,
        parts: Sequence[ModelRequestPart],
        seen_tool_call_ids: set[str],
    ) -> list[ModelRequestPart]:
        sanitized_parts: list[ModelRequestPart] = []
        for part in parts:
            tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
            is_tool_result = isinstance(part, (ToolReturnPart, RetryPromptPart))
            if (
                is_tool_result
                and tool_call_id
                and tool_call_id not in seen_tool_call_ids
            ):
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="llm.tool_call_args.dropped_orphan_tool_result",
                    message="Dropped replayed tool result without a matching tool call",
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool_name": str(getattr(part, "tool_name", "") or ""),
                    },
                )
                continue
            sanitized_parts.append(part)
        return sanitized_parts
