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
    ModelRequestPart,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel

from agent_teams.logger import get_logger, log_event
from agent_teams.agents.execution.tool_call_history import normalize_replayed_messages
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
        return normalize_replayed_messages(
            messages, on_drop=cls._log_dropped_tool_result
        )

    @staticmethod
    def _log_dropped_tool_result(part: ModelRequestPart, is_duplicate: bool) -> None:
        log_event(
            LOGGER,
            logging.WARNING,
            event=(
                "llm.tool_call_args.dropped_duplicate_tool_result"
                if is_duplicate
                else "llm.tool_call_args.dropped_orphan_tool_result"
            ),
            message=(
                "Dropped replayed duplicate tool result after the tool call was already closed"
                if is_duplicate
                else "Dropped replayed tool result without a matching tool call"
            ),
            payload={
                "tool_call_id": str(getattr(part, "tool_call_id", "") or ""),
                "tool_name": str(getattr(part, "tool_name", "") or ""),
            },
        )
