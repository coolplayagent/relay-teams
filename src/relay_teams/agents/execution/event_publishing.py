# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from pydantic import JsonValue
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

from relay_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.event_stream import (
    AsyncRunEventPublisher,
    SyncRunEventPublisher,
    publish_run_event_async,
)
from relay_teams.sessions.runs.run_models import RunEvent


class EventPublishingService:
    def __init__(
        self,
        *,
        run_event_hub: AsyncRunEventPublisher | SyncRunEventPublisher | None,
    ) -> None:
        self._run_event_hub = run_event_hub

    def publish_text_delta_event(
        self,
        *,
        request: LLMRequest,
        text: str,
    ) -> None:
        self._publish_run_event(
            request=request,
            event_type=RunEventType.TEXT_DELTA,
            payload={
                "text": text,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    async def publish_text_delta_event_async(
        self,
        *,
        request: LLMRequest,
        text: str,
    ) -> None:
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.TEXT_DELTA,
            payload={
                "text": text,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    def publish_thinking_started_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._publish_run_event(
            request=request,
            event_type=RunEventType.THINKING_STARTED,
            payload={
                "part_index": part_index,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    async def publish_thinking_started_event_async(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.THINKING_STARTED,
            payload={
                "part_index": part_index,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    def publish_thinking_delta_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        text: str,
    ) -> None:
        self._publish_run_event(
            request=request,
            event_type=RunEventType.THINKING_DELTA,
            payload={
                "part_index": part_index,
                "text": text,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    async def publish_thinking_delta_event_async(
        self,
        *,
        request: LLMRequest,
        part_index: int,
        text: str,
    ) -> None:
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.THINKING_DELTA,
            payload={
                "part_index": part_index,
                "text": text,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    def publish_thinking_finished_event(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        self._publish_run_event(
            request=request,
            event_type=RunEventType.THINKING_FINISHED,
            payload={
                "part_index": part_index,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    async def publish_thinking_finished_event_async(
        self,
        *,
        request: LLMRequest,
        part_index: int,
    ) -> None:
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.THINKING_FINISHED,
            payload={
                "part_index": part_index,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )

    def publish_tool_call_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        emitted = False
        for msg in messages:
            if not isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if tool_call_id and published_tool_call_ids is not None:
                    if tool_call_id in published_tool_call_ids:
                        continue
                    published_tool_call_ids.add(tool_call_id)
                self._publish_run_event(
                    request=request,
                    event_type=RunEventType.TOOL_CALL,
                    payload={
                        "tool_name": part.tool_name,
                        "tool_call_id": tool_call_id,
                        "args": part.args,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    },
                )
                emitted = True
        return emitted

    async def publish_tool_call_events_from_messages_async(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        emitted = False
        for msg in messages:
            if not isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if tool_call_id and published_tool_call_ids is not None:
                    if tool_call_id in published_tool_call_ids:
                        continue
                    published_tool_call_ids.add(tool_call_id)
                await self._publish_run_event_async(
                    request=request,
                    event_type=RunEventType.TOOL_CALL,
                    payload={
                        "tool_name": part.tool_name,
                        "tool_call_id": tool_call_id,
                        "args": part.args,
                        "role_id": request.role_id,
                        "instance_id": request.instance_id,
                    },
                )
                emitted = True
        return emitted

    def publish_committed_tool_outcome_events_from_messages(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        to_json_compatible: Callable[[object], JsonValue],
        maybe_enrich_tool_result_payload: Callable[..., JsonValue],
        tool_result_already_emitted_from_runtime: Callable[..., bool],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        emitted = False
        for msg in messages:
            if isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if (
                        tool_call_id
                        and published_tool_outcome_ids is not None
                        and tool_call_id in published_tool_outcome_ids
                    ):
                        continue
                    if tool_result_already_emitted_from_runtime(
                        request=request,
                        tool_name=str(part.tool_name),
                        tool_call_id=tool_call_id,
                    ):
                        continue
                    result_payload = cast(
                        JsonValue,
                        sanitize_task_status_payload(to_json_compatible(part.content)),
                    )
                    result_payload = maybe_enrich_tool_result_payload(
                        tool_name=str(part.tool_name),
                        result_payload=result_payload,
                    )
                    is_error = False
                    if isinstance(result_payload, dict):
                        is_error = result_payload.get("ok") is False
                    self._publish_run_event(
                        request=request,
                        event_type=RunEventType.TOOL_RESULT,
                        payload={
                            "tool_name": str(part.tool_name),
                            "tool_call_id": tool_call_id,
                            "result": result_payload,
                            "error": is_error,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                    )
                    if tool_call_id and published_tool_outcome_ids is not None:
                        published_tool_outcome_ids.add(tool_call_id)
                    emitted = True
                    continue
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if (
                        tool_call_id
                        and published_tool_outcome_ids is not None
                        and tool_call_id in published_tool_outcome_ids
                    ):
                        continue
                    self._publish_run_event(
                        request=request,
                        event_type=RunEventType.TOOL_INPUT_VALIDATION_FAILED,
                        payload={
                            "tool_name": part.tool_name,
                            "tool_call_id": tool_call_id,
                            "reason": "Input validation failed before tool execution.",
                            "details": part.content,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                    )
                    if tool_call_id and published_tool_outcome_ids is not None:
                        published_tool_outcome_ids.add(tool_call_id)
                    emitted = True
        return emitted

    async def publish_committed_tool_outcome_events_from_messages_async(
        self,
        *,
        request: LLMRequest,
        messages: Sequence[ModelResponse | ModelRequest],
        to_json_compatible: Callable[[object], JsonValue],
        maybe_enrich_tool_result_payload: Callable[..., JsonValue],
        tool_result_already_emitted_from_runtime: Callable[..., Awaitable[bool]],
        published_tool_outcome_ids: set[str] | None = None,
    ) -> bool:
        emitted = False
        for msg in messages:
            if isinstance(msg, ModelResponse):
                continue
            for part in msg.parts:
                if isinstance(part, ToolReturnPart):
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if (
                        tool_call_id
                        and published_tool_outcome_ids is not None
                        and tool_call_id in published_tool_outcome_ids
                    ):
                        continue
                    already_emitted = await tool_result_already_emitted_from_runtime(
                        request=request,
                        tool_name=str(part.tool_name),
                        tool_call_id=tool_call_id,
                    )
                    if already_emitted:
                        continue
                    result_payload = cast(
                        JsonValue,
                        sanitize_task_status_payload(
                            to_json_compatible(cast(object, part.content))
                        ),
                    )
                    result_payload = maybe_enrich_tool_result_payload(
                        tool_name=str(part.tool_name),
                        result_payload=result_payload,
                    )
                    is_error = False
                    if isinstance(result_payload, dict):
                        is_error = result_payload.get("ok") is False
                    await self._publish_run_event_async(
                        request=request,
                        event_type=RunEventType.TOOL_RESULT,
                        payload={
                            "tool_name": str(part.tool_name),
                            "tool_call_id": tool_call_id,
                            "result": result_payload,
                            "error": is_error,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                    )
                    if tool_call_id and published_tool_outcome_ids is not None:
                        published_tool_outcome_ids.add(tool_call_id)
                    emitted = True
                    continue
                if isinstance(part, RetryPromptPart) and part.tool_name:
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if (
                        tool_call_id
                        and published_tool_outcome_ids is not None
                        and tool_call_id in published_tool_outcome_ids
                    ):
                        continue
                    await self._publish_run_event_async(
                        request=request,
                        event_type=RunEventType.TOOL_INPUT_VALIDATION_FAILED,
                        payload={
                            "tool_name": part.tool_name,
                            "tool_call_id": tool_call_id,
                            "reason": "Input validation failed before tool execution.",
                            "details": part.content,
                            "role_id": request.role_id,
                            "instance_id": request.instance_id,
                        },
                    )
                    if tool_call_id and published_tool_outcome_ids is not None:
                        published_tool_outcome_ids.add(tool_call_id)
                    emitted = True
        return emitted

    def _publish_run_event(
        self,
        *,
        request: LLMRequest,
        event_type: RunEventType,
        payload: dict[str, object],
    ) -> None:
        if self._run_event_hub is None:
            return
        if not isinstance(self._run_event_hub, SyncRunEventPublisher):
            return
        self._run_event_hub.publish(
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=event_type,
                payload_json=self._to_json(payload),
            )
        )

    async def _publish_run_event_async(
        self,
        *,
        request: LLMRequest,
        event_type: RunEventType,
        payload: dict[str, object],
    ) -> None:
        if self._run_event_hub is None:
            return
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=request.session_id,
                run_id=request.run_id,
                trace_id=request.trace_id,
                task_id=request.task_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                event_type=event_type,
                payload_json=self._to_json(payload),
            ),
        )

    def _to_json(self, obj: object) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return json.dumps({"error": "unserializable", "repr": str(obj)})
