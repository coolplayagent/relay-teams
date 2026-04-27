# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelRequestPart,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

ToolResultDropLogger = Callable[[ModelRequestPart, bool], None]


def clone_model_request_with_parts(
    message: ModelRequest,
    parts: Sequence[ModelRequestPart],
) -> ModelRequest:
    return ModelRequest(
        parts=parts,
        timestamp=message.timestamp,
        instructions=message.instructions,
        run_id=message.run_id,
        metadata=message.metadata,
    )


def normalize_replayed_messages(
    messages: Sequence[ModelMessage],
    *,
    on_drop: ToolResultDropLogger | None = None,
) -> list[ModelMessage]:
    pending_tool_calls: dict[str, str] = {}
    seen_tool_call_ids: set[str] = set()
    sanitized_messages: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            _advance_tool_call_state(
                pending_tool_calls=pending_tool_calls,
                seen_tool_call_ids=seen_tool_call_ids,
                message=message,
            )
            sanitized_messages.append(message)
            continue
        if not isinstance(message, ModelRequest):
            sanitized_messages.append(message)
            continue
        next_parts = _sanitize_request_parts(
            parts=message.parts,
            pending_tool_calls=pending_tool_calls,
            seen_tool_call_ids=seen_tool_call_ids,
            on_drop=on_drop,
        )
        if next_parts:
            sanitized_messages.append(
                clone_model_request_with_parts(message, next_parts)
            )
    return sanitized_messages


def normalize_replayed_messages_to_safe_boundary(
    messages: Sequence[ModelMessage],
    *,
    on_drop: ToolResultDropLogger | None = None,
) -> list[ModelMessage]:
    normalized = normalize_replayed_messages(messages, on_drop=on_drop)
    pending_tool_calls: dict[str, str] = {}
    seen_tool_call_ids: set[str] = set()
    last_safe_index = 0
    for index, message in enumerate(normalized, start=1):
        _advance_tool_call_state(
            pending_tool_calls=pending_tool_calls,
            seen_tool_call_ids=seen_tool_call_ids,
            message=message,
        )
        if not pending_tool_calls:
            last_safe_index = index
    return normalized[:last_safe_index]


def collect_safe_row_ids(
    rows: Sequence[tuple[int, Sequence[ModelMessage]]],
    *,
    on_drop: ToolResultDropLogger | None = None,
) -> set[int]:
    pending_tool_calls: dict[str, str] = {}
    seen_tool_call_ids: set[str] = set()
    candidate_ids: set[int] = set()
    safe_ids: set[int] = set()
    for row_id, messages in rows:
        normalize_replayed_messages_against_pending(
            messages,
            pending_tool_calls=pending_tool_calls,
            seen_tool_call_ids=seen_tool_call_ids,
            on_drop=on_drop,
        )
        candidate_ids.add(row_id)
        if not pending_tool_calls:
            safe_ids = candidate_ids.copy()
    return safe_ids


def normalize_replayed_messages_against_pending(
    messages: Sequence[ModelMessage],
    *,
    pending_tool_call_ids: set[str] | None = None,
    pending_tool_calls: dict[str, str] | None = None,
    seen_tool_call_ids: set[str],
    on_drop: ToolResultDropLogger | None = None,
) -> list[ModelMessage]:
    active_pending = (
        {tool_call_id: "" for tool_call_id in pending_tool_call_ids}
        if pending_tool_calls is None and pending_tool_call_ids is not None
        else (pending_tool_calls or {})
    )
    sanitized_messages: list[ModelMessage] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            _advance_tool_call_state(
                pending_tool_calls=active_pending,
                seen_tool_call_ids=seen_tool_call_ids,
                message=message,
            )
            sanitized_messages.append(message)
            continue
        if not isinstance(message, ModelRequest):
            sanitized_messages.append(message)
            continue
        next_parts = _sanitize_request_parts(
            parts=message.parts,
            pending_tool_calls=active_pending,
            seen_tool_call_ids=seen_tool_call_ids,
            on_drop=on_drop,
        )
        if next_parts:
            sanitized_messages.append(
                clone_model_request_with_parts(message, next_parts)
            )
    return sanitized_messages


def _sanitize_request_parts(
    *,
    parts: Sequence[ModelRequestPart],
    pending_tool_calls: dict[str, str],
    seen_tool_call_ids: set[str],
    on_drop: ToolResultDropLogger | None = None,
) -> list[ModelRequestPart]:
    sanitized_parts: list[ModelRequestPart] = []
    for part in parts:
        tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
        if not isinstance(part, (ToolReturnPart, RetryPromptPart)) or not tool_call_id:
            sanitized_parts.append(part)
            continue
        expected_tool_name = pending_tool_calls.get(tool_call_id)
        actual_tool_name = str(getattr(part, "tool_name", "") or "")
        if expected_tool_name is None or (
            expected_tool_name
            and actual_tool_name
            and actual_tool_name != expected_tool_name
        ):
            if on_drop is not None:
                on_drop(part, tool_call_id in seen_tool_call_ids)
            continue
        pending_tool_calls.pop(tool_call_id, None)
        sanitized_parts.append(part)
    return sanitized_parts


def _advance_tool_call_state(
    *,
    pending_tool_calls: dict[str, str],
    seen_tool_call_ids: set[str],
    message: ModelMessage,
) -> None:
    if isinstance(message, ModelResponse):
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            tool_call_id = str(part.tool_call_id or "").strip()
            if tool_call_id:
                seen_tool_call_ids.add(tool_call_id)
                pending_tool_calls[tool_call_id] = str(part.tool_name or "")
        return
    if not isinstance(message, ModelRequest):
        return
    _sanitize_request_parts(
        parts=message.parts,
        pending_tool_calls=pending_tool_calls,
        seen_tool_call_ids=seen_tool_call_ids,
        on_drop=None,
    )
