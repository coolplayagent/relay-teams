# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
    ToolCallPartDelta,
)

from relay_teams.agents.execution.tool_args_repair import (
    ToolArgsRepairResult,
    repair_tool_args,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.providers.provider_contracts import LLMRequest

LOGGER = get_logger(__name__)


class StreamEventService:
    def looks_like_tool_args_parse_failure(self, message: str) -> bool:
        lowered = message.strip().lower()
        if not lowered:
            return False
        indicators = (
            "expecting ',' delimiter",
            "expecting ':' delimiter",
            "expecting property name enclosed in double quotes",
            "expecting value",
            "invalid json",
            "tool arguments",
            "function.arguments",
        )
        return any(indicator in lowered for indicator in indicators)

    def collect_salvageable_stream_tool_calls(
        self,
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
    ) -> list[ToolCallPart]:
        salvageable: list[ToolCallPart] = []
        for index in sorted(streamed_tool_calls):
            item = streamed_tool_calls[index]
            if isinstance(item, ToolCallPart):
                salvageable.append(self.normalize_salvaged_tool_call_for_recovery(item))
                continue
            candidate = item.as_part()
            if candidate is not None:
                salvageable.append(
                    self.normalize_salvaged_tool_call_for_recovery(candidate)
                )
        return salvageable

    def normalize_salvaged_tool_call_for_recovery(
        self,
        tool_call: ToolCallPart,
    ) -> ToolCallPart:
        repaired = repair_tool_args(tool_call.args)
        if repaired.repair_applied or repaired.fallback_invalid_json:
            self._log_salvaged_tool_call_repair(
                tool_call=tool_call,
                repaired=repaired,
            )
        return ToolCallPart(
            tool_name=tool_call.tool_name,
            args=repaired.normalized_args,
            tool_call_id=str(tool_call.tool_call_id or ""),
            id=tool_call.id,
            provider_name=tool_call.provider_name,
            provider_details=tool_call.provider_details,
        )

    def handle_model_stream_event(
        self,
        *,
        request: LLMRequest,
        stream_event: object,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        handle_part_start_event: Callable[..., bool],
        handle_part_delta_event: Callable[..., bool],
        handle_part_end_event: Callable[..., bool],
    ) -> bool:
        if isinstance(stream_event, PartStartEvent):
            return handle_part_start_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        if isinstance(stream_event, PartDeltaEvent):
            return handle_part_delta_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        if isinstance(stream_event, PartEndEvent):
            return handle_part_end_event(
                request=request,
                event=stream_event,
                emitted_text_chunks=emitted_text_chunks,
                text_lengths=text_lengths,
                thinking_lengths=thinking_lengths,
                started_thinking_parts=started_thinking_parts,
                streamed_tool_calls=streamed_tool_calls,
            )
        return False

    def handle_part_start_event(
        self,
        *,
        request: LLMRequest,
        event: PartStartEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        emit_text_suffix_for_part: Callable[..., bool],
        emit_thinking_suffix_for_part: Callable[..., bool],
        publish_thinking_started_event: Callable[..., None],
    ) -> bool:
        part = event.part
        if isinstance(part, TextPart):
            text_lengths.setdefault(event.index, 0)
            return emit_text_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_text_chunks=emitted_text_chunks,
                emitted_lengths=text_lengths,
            )
        if isinstance(part, ThinkingPart):
            if event.index not in started_thinking_parts:
                publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            thinking_lengths.setdefault(event.index, 0)
            return emit_thinking_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_lengths=thinking_lengths,
            )
        if isinstance(part, ToolCallPart):
            streamed_tool_calls[event.index] = part
        return False

    def handle_part_delta_event(
        self,
        *,
        request: LLMRequest,
        event: PartDeltaEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        log_model_stream_chunk: Callable[[str, str], None],
        publish_text_delta_event: Callable[..., None],
        publish_thinking_started_event: Callable[..., None],
        publish_thinking_delta_event: Callable[..., None],
    ) -> bool:
        delta = event.delta
        if isinstance(delta, TextPartDelta):
            text = str(delta.content_delta or "")
            if not text:
                return False
            text_lengths[event.index] = text_lengths.get(event.index, 0) + len(text)
            emitted_text_chunks.append(text)
            log_model_stream_chunk(request.role_id, text)
            publish_text_delta_event(request=request, text=text)
            return True
        if isinstance(delta, ThinkingPartDelta):
            if event.index not in started_thinking_parts:
                publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            text = str(delta.content_delta or "")
            if not text:
                return False
            thinking_lengths[event.index] = thinking_lengths.get(event.index, 0) + len(
                text
            )
            publish_thinking_delta_event(
                request=request,
                part_index=event.index,
                text=text,
            )
            return False
        if isinstance(delta, ToolCallPartDelta):
            existing = streamed_tool_calls.get(event.index)
            if existing is None:
                as_part = delta.as_part()
                streamed_tool_calls[event.index] = (
                    as_part if as_part is not None else delta
                )
            else:
                updated = delta.apply(existing)
                if isinstance(updated, (ToolCallPart, ToolCallPartDelta)):
                    streamed_tool_calls[event.index] = updated
        return False

    def handle_part_end_event(
        self,
        *,
        request: LLMRequest,
        event: PartEndEvent,
        emitted_text_chunks: list[str],
        text_lengths: dict[int, int],
        thinking_lengths: dict[int, int],
        started_thinking_parts: set[int],
        streamed_tool_calls: dict[int, ToolCallPart | ToolCallPartDelta],
        emit_text_suffix_for_part: Callable[..., bool],
        emit_thinking_suffix_for_part: Callable[..., bool],
        publish_thinking_started_event: Callable[..., None],
        publish_thinking_finished_event: Callable[..., None],
    ) -> bool:
        part = event.part
        if isinstance(part, TextPart):
            return emit_text_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_text_chunks=emitted_text_chunks,
                emitted_lengths=text_lengths,
            )
        if isinstance(part, ThinkingPart):
            if event.index not in started_thinking_parts:
                publish_thinking_started_event(
                    request=request,
                    part_index=event.index,
                )
                started_thinking_parts.add(event.index)
            _ = emit_thinking_suffix_for_part(
                request=request,
                part_index=event.index,
                content=part.content,
                emitted_lengths=thinking_lengths,
            )
            publish_thinking_finished_event(
                request=request,
                part_index=event.index,
            )
        if isinstance(part, ToolCallPart):
            streamed_tool_calls[event.index] = part
        return False

    def emit_text_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_text_chunks: list[str],
        emitted_lengths: dict[int, int],
        log_model_stream_chunk: Callable[[str, str], None],
        publish_text_delta_event: Callable[..., None],
    ) -> bool:
        previous_length = emitted_lengths.get(part_index, 0)
        suffix = content[previous_length:]
        emitted_lengths[part_index] = len(content)
        if not suffix:
            return False
        emitted_text_chunks.append(suffix)
        log_model_stream_chunk(request.role_id, suffix)
        publish_text_delta_event(request=request, text=suffix)
        return True

    def emit_thinking_suffix_for_part(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        content: str,
        emitted_lengths: dict[int, int],
        publish_thinking_delta_event: Callable[..., None],
    ) -> bool:
        previous_length = emitted_lengths.get(part_index, 0)
        suffix = content[previous_length:]
        emitted_lengths[part_index] = len(content)
        if not suffix:
            return False
        publish_thinking_delta_event(
            request=request,
            part_index=part_index,
            text=suffix,
        )
        return False

    def _log_salvaged_tool_call_repair(
        self,
        *,
        tool_call: ToolCallPart,
        repaired: ToolArgsRepairResult,
    ) -> None:
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.tool_call_args.salvaged_from_stream",
            message="Recovered malformed streamed tool arguments for continued execution",
            payload={
                "tool_name": tool_call.tool_name,
                "tool_call_id": str(tool_call.tool_call_id or ""),
                "repair_applied": repaired.repair_applied,
                "repair_succeeded": repaired.repair_succeeded,
                "fallback_invalid_json": repaired.fallback_invalid_json,
            },
        )
