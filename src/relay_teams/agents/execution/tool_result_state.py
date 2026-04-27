# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from pydantic import JsonValue

from relay_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from relay_teams.providers.provider_contracts import LLMRequest
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    ToolExecutionStatus,
)


class ToolResultStateService:
    def visible_tool_result_from_state(
        self,
        *,
        state: PersistedToolCallState | None,
        expected_tool_name: str,
        to_json_compatible: Callable[[object], JsonValue],
    ) -> dict[str, JsonValue] | None:
        if state is None:
            return None
        tool_name = str(state.tool_name or "").strip()
        if tool_name != expected_tool_name:
            return None
        if state.execution_status not in (
            ToolExecutionStatus.COMPLETED,
            ToolExecutionStatus.FAILED,
        ):
            return None
        raw_result_envelope = state.result_envelope
        if not isinstance(raw_result_envelope, dict):
            return None
        visible_result = self.visible_tool_result_from_envelope(raw_result_envelope)
        if not isinstance(visible_result, dict):
            return None
        if (
            state.result_event_id <= 0
            and not self.tool_result_event_was_published(
                result_envelope=raw_result_envelope,
                visible_result=visible_result,
            )
            and not self.tool_result_was_durably_recorded(
                result_envelope=raw_result_envelope,
                visible_result=visible_result,
            )
        ):
            return None
        normalized = to_json_compatible(visible_result)
        if not isinstance(normalized, dict):
            return None
        sanitized = sanitize_task_status_payload(normalized)
        if not isinstance(sanitized, dict):
            return None
        return cast(dict[str, JsonValue], sanitized)

    def visible_tool_result_from_envelope(
        self,
        result_envelope: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        raw_visible_result = result_envelope.get("visible_result")
        if isinstance(raw_visible_result, dict):
            return cast(dict[str, JsonValue], raw_visible_result)
        return result_envelope

    def tool_result_event_was_published(
        self,
        *,
        result_envelope: dict[str, JsonValue],
        visible_result: dict[str, JsonValue] | None = None,
    ) -> bool:
        runtime_meta = result_envelope.get("runtime_meta")
        if isinstance(runtime_meta, dict):
            return runtime_meta.get("tool_result_event_published") is True
        envelope = result_envelope if visible_result is None else visible_result
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            return False
        return meta.get("tool_result_event_published") is True

    @staticmethod
    def tool_result_was_durably_recorded(
        *,
        result_envelope: dict[str, JsonValue],
        visible_result: dict[str, JsonValue] | None = None,
    ) -> bool:
        runtime_meta = result_envelope.get("runtime_meta")
        if isinstance(runtime_meta, dict):
            return runtime_meta.get("tool_result_durably_recorded") is True
        envelope = result_envelope if visible_result is None else visible_result
        meta = envelope.get("meta")
        if not isinstance(meta, dict):
            return False
        return meta.get("tool_result_durably_recorded") is True

    def tool_result_already_emitted_from_runtime(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
        shared_store: object,
        load_tool_call_state: Callable[..., PersistedToolCallState | None],
    ) -> bool:
        if not tool_call_id:
            return False
        state = load_tool_call_state(
            shared_store=shared_store,
            task_id=request.task_id,
            tool_call_id=tool_call_id,
        )
        if state is None or state.tool_name != tool_name:
            return False
        if state.execution_status not in (
            ToolExecutionStatus.COMPLETED,
            ToolExecutionStatus.FAILED,
        ):
            return False
        result_envelope = state.result_envelope
        if not isinstance(result_envelope, dict):
            return False
        if state.result_event_id > 0:
            return True
        visible_result = self.visible_tool_result_from_envelope(result_envelope)
        return self.tool_result_event_was_published(
            result_envelope=result_envelope,
            visible_result=visible_result,
        )

    async def tool_result_already_emitted_from_runtime_async(
        self,
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
        shared_store: object,
        load_tool_call_state: Callable[
            ...,
            Awaitable[PersistedToolCallState | None],
        ],
    ) -> bool:
        if not tool_call_id:
            return False
        state = await load_tool_call_state(
            shared_store=shared_store,
            task_id=request.task_id,
            tool_call_id=tool_call_id,
        )
        if state is None or state.tool_name != tool_name:
            return False
        if state.execution_status not in (
            ToolExecutionStatus.COMPLETED,
            ToolExecutionStatus.FAILED,
        ):
            return False
        result_envelope = state.result_envelope
        if not isinstance(result_envelope, dict):
            return False
        if state.result_event_id > 0:
            return True
        visible_result = self.visible_tool_result_from_envelope(result_envelope)
        return self.tool_result_event_was_published(
            result_envelope=result_envelope,
            visible_result=visible_result,
        )
