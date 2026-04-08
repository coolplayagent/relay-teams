# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    ToolReturnPart,
)

from agent_teams.agents.execution.conversation_compaction import (
    DEFAULT_PROTECTED_TAIL_MESSAGES,
    ConversationCompactionBudget,
    ConversationTokenEstimator,
    _resolve_protected_tail_messages,
    _update_pending_tool_call_ids,
)
from agent_teams.agents.execution.tool_call_history import (
    clone_model_request_with_parts,
)

_MIN_TOOL_RESULT_TOKENS = 192
_PREVIEW_CHARS = 160
_COMPACT_NOTE = "older tool output removed to preserve context window"


class ConversationMicrocompactResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        arbitrary_types_allowed=True,
    )

    messages: tuple[ModelRequest | ModelResponse, ...]
    estimated_tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    compacted_message_count: int = Field(default=0, ge=0)
    compacted_part_count: int = Field(default=0, ge=0)


class ConversationMicrocompactService:
    def __init__(
        self,
        *,
        estimator: ConversationTokenEstimator | None = None,
        protected_tail_messages: int = DEFAULT_PROTECTED_TAIL_MESSAGES,
    ) -> None:
        self._estimator = estimator or ConversationTokenEstimator()
        self._protected_tail_messages = max(1, protected_tail_messages)

    def apply(
        self,
        *,
        history: Sequence[ModelRequest | ModelResponse],
        budget: ConversationCompactionBudget,
    ) -> ConversationMicrocompactResult:
        original_history = list(history)
        estimated_before = self._estimator.estimate_history_tokens(original_history)
        if len(original_history) <= 1:
            return ConversationMicrocompactResult(
                messages=tuple(original_history),
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_before,
            )

        target_tokens = budget.history_target_tokens
        trigger_tokens = budget.history_trigger_tokens
        if (
            trigger_tokens <= 0
            or target_tokens <= 0
            or estimated_before < trigger_tokens
        ):
            return ConversationMicrocompactResult(
                messages=tuple(original_history),
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_before,
            )

        protected_tail_messages = _resolve_protected_tail_messages(
            message_count=len(original_history),
            estimated_tokens=estimated_before,
            threshold_tokens=trigger_tokens,
            default_protected_tail_messages=self._protected_tail_messages,
        )
        latest_safe_split = self._latest_safe_split_index(
            original_history,
            protected_tail_messages=protected_tail_messages,
        )
        if latest_safe_split <= 0:
            return ConversationMicrocompactResult(
                messages=tuple(original_history),
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_before,
            )

        next_history = list(original_history)
        remaining_tokens = estimated_before
        compacted_message_indexes: set[int] = set()
        compacted_part_count = 0
        for index, message in enumerate(original_history):
            if index >= latest_safe_split or remaining_tokens <= target_tokens:
                break
            replacement = self._microcompact_message(message)
            if replacement is None:
                continue
            replacement_message, saved_tokens, replaced_part_count = replacement
            if saved_tokens <= 0 or replaced_part_count <= 0:
                continue
            next_history[index] = replacement_message
            remaining_tokens = max(0, remaining_tokens - saved_tokens)
            compacted_message_indexes.add(index)
            compacted_part_count += replaced_part_count

        return ConversationMicrocompactResult(
            messages=tuple(next_history),
            estimated_tokens_before=estimated_before,
            estimated_tokens_after=max(0, remaining_tokens),
            compacted_message_count=len(compacted_message_indexes),
            compacted_part_count=compacted_part_count,
        )

    def _latest_safe_split_index(
        self,
        history: Sequence[ModelRequest | ModelResponse],
        *,
        protected_tail_messages: int,
    ) -> int:
        max_compactable = max(0, len(history) - protected_tail_messages)
        pending_tool_call_ids: set[str] = set()
        latest_safe_split = 0
        for index, message in enumerate(history, start=1):
            if index > max_compactable:
                break
            _update_pending_tool_call_ids(pending_tool_call_ids, message)
            if pending_tool_call_ids:
                continue
            latest_safe_split = index
        return latest_safe_split

    def _microcompact_message(
        self,
        message: ModelRequest | ModelResponse,
    ) -> tuple[ModelRequest | ModelResponse, int, int] | None:
        if isinstance(message, ModelRequest):
            next_parts: list[ModelRequestPart] = []
            saved_tokens = 0
            compacted_part_count = 0
            for part in message.parts:
                replacement = self._microcompact_tool_return_part(part)
                if replacement is None:
                    next_parts.append(part)
                    continue
                replacement_part, part_saved_tokens = replacement
                next_parts.append(replacement_part)
                saved_tokens += part_saved_tokens
                compacted_part_count += 1
            if compacted_part_count <= 0:
                return None
            return (
                clone_model_request_with_parts(message, next_parts),
                saved_tokens,
                compacted_part_count,
            )
        return None

    def _microcompact_tool_return_part(
        self,
        part: ModelRequestPart,
    ) -> tuple[ToolReturnPart, int] | None:
        if not isinstance(part, ToolReturnPart):
            return None
        original_payload = _stringify_tool_result_content(part.content)
        original_tokens = _estimate_text_tokens(original_payload)
        if original_tokens < _MIN_TOOL_RESULT_TOKENS:
            return None
        compacted_content = _build_compacted_tool_result_content(
            tool_name=str(part.tool_name or "").strip() or "tool",
            original_payload=original_payload,
            original_tokens=original_tokens,
        )
        compacted_tokens = _estimate_text_tokens(compacted_content)
        saved_tokens = max(0, original_tokens - compacted_tokens)
        if saved_tokens <= 0:
            return None
        return (
            ToolReturnPart(
                tool_name=part.tool_name,
                tool_call_id=part.tool_call_id,
                content=compacted_content,
                metadata=part.metadata,
                timestamp=part.timestamp,
                outcome=part.outcome,
                part_kind=part.part_kind,
            ),
            saved_tokens,
        )


def _build_compacted_tool_result_content(
    *,
    tool_name: str,
    original_payload: str,
    original_tokens: int,
) -> str:
    normalized_payload = " ".join(original_payload.split())
    if len(normalized_payload) <= (_PREVIEW_CHARS * 2):
        preview_start = normalized_payload
        preview_end = normalized_payload
    else:
        preview_start = normalized_payload[:_PREVIEW_CHARS].rstrip()
        preview_end = normalized_payload[-_PREVIEW_CHARS:].lstrip()
    return (
        "[Compacted tool result]\n"
        f"tool: {tool_name}\n"
        f"original_tokens: {original_tokens}\n"
        f"note: {_COMPACT_NOTE}\n"
        f"preview_start: {preview_start}\n"
        f"preview_end: {preview_end}"
    )


def _stringify_tool_result_content(content: object) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str, sort_keys=True)
    except TypeError:
        return str(content)


def _estimate_text_tokens(content: str) -> int:
    encoded = content.encode("utf-8")
    return max(1, (len(encoded) // 4) + 8)
