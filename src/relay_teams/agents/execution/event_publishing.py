# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from typing import cast
from uuid import uuid4

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
from relay_teams.logger import get_logger, log_event
from relay_teams.tools.runtime.persisted_state import (
    PersistedToolCallState,
    PersistedToolCallBatchItem,
    ToolCallBatchStatus,
    ToolExecutionStatus,
    load_tool_call_batch_state,
    load_tool_call_batch_state_async,
    load_tool_call_state,
    load_tool_call_state_async,
    merge_tool_call_batch_state,
    merge_tool_call_batch_state_async,
    merge_tool_call_state,
    merge_tool_call_state_async,
)
from relay_teams.persistence.shared_state_repo import SharedStateRepository

LOGGER = get_logger(__name__)


class EventPublishingService:
    def __init__(
        self,
        *,
        run_event_hub: AsyncRunEventPublisher | SyncRunEventPublisher | None,
        shared_store: SharedStateRepository | None = None,
    ) -> None:
        self._run_event_hub = run_event_hub
        self._shared_store = shared_store

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
            tool_calls = _tool_calls_from_response(msg)
            if not tool_calls:
                continue
            batch_id = self._batch_id_for_tool_calls(
                request=request,
                tool_calls=tool_calls,
            )
            batch_emitted = False
            for batch_index, part in tool_calls:
                if self.publish_observed_tool_call_event(
                    request=request,
                    part=part,
                    batch_id=batch_id,
                    batch_index=batch_index,
                    batch_size=len(tool_calls),
                    published_tool_call_ids=published_tool_call_ids,
                ):
                    emitted = True
                    batch_emitted = True
            if batch_emitted or self._tool_call_batch_has_observed_items(
                request=request,
                batch_id=batch_id,
            ):
                self.seal_tool_call_batch(
                    request=request,
                    batch_id=batch_id,
                    tool_calls=tool_calls,
                )
        return emitted

    def publish_observed_tool_call_event(
        self,
        *,
        request: LLMRequest,
        part: ToolCallPart,
        batch_id: str,
        batch_index: int,
        batch_size: int,
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        tool_call_id = str(part.tool_call_id or "").strip()
        tool_name = str(part.tool_name or "").strip()
        if not tool_call_id or not tool_name:
            return False
        if published_tool_call_ids is not None:
            if tool_call_id in published_tool_call_ids:
                return False
            published_tool_call_ids.add(tool_call_id)
        self._publish_run_event(
            request=request,
            event_type=RunEventType.TOOL_CALL,
            payload={
                "run_id": request.run_id,
                "session_id": request.session_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "args": part.args,
                "batch_id": batch_id,
                "batch_index": batch_index,
                "batch_size": batch_size,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )
        try:
            self._persist_observed_tool_call(
                request=request,
                part=part,
                batch_id=batch_id,
                batch_index=batch_index,
                batch_size=batch_size,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_observed_tool_call_persistence_skipped(
                request=request,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                batch_id=batch_id,
                error=exc,
            )
        return True

    def seal_tool_call_batch(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
        tool_calls: Sequence[tuple[int, ToolCallPart]],
    ) -> None:
        if not tool_calls:
            return
        items = _batch_items(tool_calls)
        if not items:
            return
        if self._tool_call_batch_is_sealed(request=request, batch_id=batch_id):
            return
        self._publish_run_event(
            request=request,
            event_type=RunEventType.TOOL_CALL_BATCH_SEALED,
            payload={
                "run_id": request.run_id,
                "session_id": request.session_id,
                "batch_id": batch_id,
                "tool_calls": [
                    {
                        "tool_call_id": item.tool_call_id,
                        "tool_name": item.tool_name,
                        "args": item.args_preview,
                        "index": item.index,
                    }
                    for item in items
                ],
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )
        try:
            self._persist_tool_call_batch_seal(
                request=request,
                batch_id=batch_id,
                items=items,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_seal_persistence_skipped(
                request=request,
                batch_id=batch_id,
                error=exc,
            )

    async def publish_observed_tool_call_event_async(
        self,
        *,
        request: LLMRequest,
        part: ToolCallPart,
        batch_id: str,
        batch_index: int,
        batch_size: int,
        published_tool_call_ids: set[str] | None = None,
    ) -> bool:
        tool_call_id = str(part.tool_call_id or "").strip()
        tool_name = str(part.tool_name or "").strip()
        if not tool_call_id or not tool_name:
            return False
        if published_tool_call_ids is not None:
            if tool_call_id in published_tool_call_ids:
                return False
            published_tool_call_ids.add(tool_call_id)
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.TOOL_CALL,
            payload={
                "run_id": request.run_id,
                "session_id": request.session_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "args": part.args,
                "batch_id": batch_id,
                "batch_index": batch_index,
                "batch_size": batch_size,
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )
        try:
            await self._persist_observed_tool_call_async(
                request=request,
                part=part,
                batch_id=batch_id,
                batch_index=batch_index,
                batch_size=batch_size,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_observed_tool_call_persistence_skipped(
                request=request,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                batch_id=batch_id,
                error=exc,
            )
        return True

    async def seal_tool_call_batch_async(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
        tool_calls: Sequence[tuple[int, ToolCallPart]],
    ) -> None:
        if not tool_calls:
            return
        items = _batch_items(tool_calls)
        if not items:
            return
        if await self._tool_call_batch_is_sealed_async(
            request=request,
            batch_id=batch_id,
        ):
            return
        await self._publish_run_event_async(
            request=request,
            event_type=RunEventType.TOOL_CALL_BATCH_SEALED,
            payload={
                "run_id": request.run_id,
                "session_id": request.session_id,
                "batch_id": batch_id,
                "tool_calls": [
                    {
                        "tool_call_id": item.tool_call_id,
                        "tool_name": item.tool_name,
                        "args": item.args_preview,
                        "index": item.index,
                    }
                    for item in items
                ],
                "role_id": request.role_id,
                "instance_id": request.instance_id,
            },
        )
        try:
            await self._persist_tool_call_batch_seal_async(
                request=request,
                batch_id=batch_id,
                items=items,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_seal_persistence_skipped(
                request=request,
                batch_id=batch_id,
                error=exc,
            )

    def _persist_tool_call_batch_seal(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
        items: tuple[PersistedToolCallBatchItem, ...],
    ) -> None:
        if self._shared_store is None:
            return
        merge_tool_call_batch_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            status=ToolCallBatchStatus.SEALED,
            items=items,
        )
        for item in items:
            current = load_tool_call_state(
                shared_store=self._shared_store,
                task_id=request.task_id,
                tool_call_id=item.tool_call_id,
            )
            merge_tool_call_state(
                shared_store=self._shared_store,
                task_id=request.task_id,
                tool_call_id=item.tool_call_id,
                tool_name=item.tool_name,
                run_id=request.run_id,
                session_id=request.session_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                args_preview=item.args_preview,
                execution_status=(
                    ToolExecutionStatus.READY
                    if current is None
                    else current.execution_status
                ),
                batch_id=batch_id,
                batch_index=item.index,
                batch_size=len(items),
            )

    async def _persist_tool_call_batch_seal_async(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
        items: tuple[PersistedToolCallBatchItem, ...],
    ) -> None:
        if self._shared_store is None:
            return
        await merge_tool_call_batch_state_async(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            status=ToolCallBatchStatus.SEALED,
            items=items,
        )
        for item in items:
            current = await load_tool_call_state_async(
                shared_store=self._shared_store,
                task_id=request.task_id,
                tool_call_id=item.tool_call_id,
            )
            await merge_tool_call_state_async(
                shared_store=self._shared_store,
                task_id=request.task_id,
                tool_call_id=item.tool_call_id,
                tool_name=item.tool_name,
                run_id=request.run_id,
                session_id=request.session_id,
                instance_id=request.instance_id,
                role_id=request.role_id,
                args_preview=item.args_preview,
                execution_status=(
                    ToolExecutionStatus.READY
                    if current is None
                    else current.execution_status
                ),
                batch_id=batch_id,
                batch_index=item.index,
                batch_size=len(items),
            )

    def _batch_id_for_tool_calls(
        self,
        *,
        request: LLMRequest,
        tool_calls: Sequence[tuple[int, ToolCallPart]],
    ) -> str:
        if self._shared_store is not None:
            try:
                for _index, part in tool_calls:
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if not tool_call_id:
                        continue
                    state = load_tool_call_state(
                        shared_store=self._shared_store,
                        task_id=request.task_id,
                        tool_call_id=tool_call_id,
                    )
                    if state is not None and state.batch_id:
                        return state.batch_id
            except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self._log_tool_call_batch_state_lookup_skipped(
                    request=request,
                    batch_id=None,
                    operation="batch_id_lookup",
                    error=exc,
                )
        return f"toolbatch_{uuid4().hex[:16]}"

    def _tool_call_batch_is_sealed(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
    ) -> bool:
        if self._shared_store is None:
            return False
        try:
            current = load_tool_call_batch_state(
                shared_store=self._shared_store,
                task_id=request.task_id,
                batch_id=batch_id,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_state_lookup_skipped(
                request=request,
                batch_id=batch_id,
                operation="sealed_status_lookup",
                error=exc,
            )
            return False
        return current is not None and current.status == ToolCallBatchStatus.SEALED

    def _tool_call_batch_has_observed_items(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
    ) -> bool:
        if self._shared_store is None:
            return False
        try:
            current = load_tool_call_batch_state(
                shared_store=self._shared_store,
                task_id=request.task_id,
                batch_id=batch_id,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_state_lookup_skipped(
                request=request,
                batch_id=batch_id,
                operation="observed_items_lookup",
                error=exc,
            )
            return False
        return current is not None and bool(current.items)

    async def _batch_id_for_tool_calls_async(
        self,
        *,
        request: LLMRequest,
        tool_calls: Sequence[tuple[int, ToolCallPart]],
    ) -> str:
        if self._shared_store is not None:
            try:
                for _index, part in tool_calls:
                    tool_call_id = str(part.tool_call_id or "").strip()
                    if not tool_call_id:
                        continue
                    state = await load_tool_call_state_async(
                        shared_store=self._shared_store,
                        task_id=request.task_id,
                        tool_call_id=tool_call_id,
                    )
                    if state is not None and state.batch_id:
                        return state.batch_id
            except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
                self._log_tool_call_batch_state_lookup_skipped(
                    request=request,
                    batch_id=None,
                    operation="batch_id_lookup",
                    error=exc,
                )
        return f"toolbatch_{uuid4().hex[:16]}"

    async def _tool_call_batch_is_sealed_async(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
    ) -> bool:
        if self._shared_store is None:
            return False
        try:
            current = await load_tool_call_batch_state_async(
                shared_store=self._shared_store,
                task_id=request.task_id,
                batch_id=batch_id,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_state_lookup_skipped(
                request=request,
                batch_id=batch_id,
                operation="sealed_status_lookup",
                error=exc,
            )
            return False
        return current is not None and current.status == ToolCallBatchStatus.SEALED

    async def _tool_call_batch_has_observed_items_async(
        self,
        *,
        request: LLMRequest,
        batch_id: str,
    ) -> bool:
        if self._shared_store is None:
            return False
        try:
            current = await load_tool_call_batch_state_async(
                shared_store=self._shared_store,
                task_id=request.task_id,
                batch_id=batch_id,
            )
        except (KeyError, RuntimeError, ValueError, sqlite3.Error) as exc:
            self._log_tool_call_batch_state_lookup_skipped(
                request=request,
                batch_id=batch_id,
                operation="observed_items_lookup",
                error=exc,
            )
            return False
        return current is not None and bool(current.items)

    def _persist_observed_tool_call(
        self,
        *,
        request: LLMRequest,
        part: ToolCallPart,
        batch_id: str,
        batch_index: int,
        batch_size: int,
    ) -> None:
        if self._shared_store is None:
            return
        current_batch = load_tool_call_batch_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
        )
        item = _batch_item(batch_index, part)
        items = (
            (item,)
            if current_batch is None
            else _merge_items(
                current_batch.items,
                (item,),
            )
        )
        merge_tool_call_batch_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            status=ToolCallBatchStatus.OPEN
            if current_batch is None
            else current_batch.status,
            items=items,
        )
        current_call = load_tool_call_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            tool_call_id=item.tool_call_id,
        )
        merge_tool_call_state(
            shared_store=self._shared_store,
            task_id=request.task_id,
            tool_call_id=item.tool_call_id,
            tool_name=item.tool_name,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            args_preview=item.args_preview,
            execution_status=_observed_tool_execution_status(current_call),
            batch_id=batch_id,
            batch_index=batch_index,
            batch_size=batch_size,
        )

    async def _persist_observed_tool_call_async(
        self,
        *,
        request: LLMRequest,
        part: ToolCallPart,
        batch_id: str,
        batch_index: int,
        batch_size: int,
    ) -> None:
        if self._shared_store is None:
            return
        current_batch = await load_tool_call_batch_state_async(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
        )
        item = _batch_item(batch_index, part)
        items = (
            (item,)
            if current_batch is None
            else _merge_items(
                current_batch.items,
                (item,),
            )
        )
        await merge_tool_call_batch_state_async(
            shared_store=self._shared_store,
            task_id=request.task_id,
            batch_id=batch_id,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            status=ToolCallBatchStatus.OPEN
            if current_batch is None
            else current_batch.status,
            items=items,
        )
        current_call = await load_tool_call_state_async(
            shared_store=self._shared_store,
            task_id=request.task_id,
            tool_call_id=item.tool_call_id,
        )
        await merge_tool_call_state_async(
            shared_store=self._shared_store,
            task_id=request.task_id,
            tool_call_id=item.tool_call_id,
            tool_name=item.tool_name,
            run_id=request.run_id,
            session_id=request.session_id,
            instance_id=request.instance_id,
            role_id=request.role_id,
            args_preview=item.args_preview,
            execution_status=_observed_tool_execution_status(current_call),
            batch_id=batch_id,
            batch_index=batch_index,
            batch_size=batch_size,
        )

    @staticmethod
    def _log_observed_tool_call_persistence_skipped(
        *,
        request: LLMRequest,
        tool_name: str,
        tool_call_id: str,
        batch_id: str,
        error: Exception,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "run_id": request.run_id,
            "task_id": request.task_id,
            "trace_id": request.trace_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "batch_id": batch_id,
            "error_type": error.__class__.__name__,
            "error": str(error),
        }
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.skipped_observed_tool_call_persistence",
            message=(
                "Skipped best-effort observed tool-call persistence after "
                "TOOL_CALL event publication"
            ),
            payload=payload,
        )

    @staticmethod
    def _log_tool_call_batch_seal_persistence_skipped(
        *,
        request: LLMRequest,
        batch_id: str,
        error: Exception,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "run_id": request.run_id,
            "task_id": request.task_id,
            "trace_id": request.trace_id,
            "batch_id": batch_id,
            "error_type": error.__class__.__name__,
            "error": str(error),
        }
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.skipped_tool_call_batch_seal_persistence",
            message=(
                "Skipped best-effort tool-call batch seal persistence after "
                "TOOL_CALL_BATCH_SEALED event publication"
            ),
            payload=payload,
        )

    @staticmethod
    def _log_tool_call_batch_state_lookup_skipped(
        *,
        request: LLMRequest,
        batch_id: str | None,
        operation: str,
        error: Exception,
    ) -> None:
        payload: dict[str, JsonValue] = {
            "run_id": request.run_id,
            "task_id": request.task_id,
            "trace_id": request.trace_id,
            "operation": operation,
            "error_type": error.__class__.__name__,
            "error": str(error),
        }
        if batch_id is not None:
            payload["batch_id"] = batch_id
        log_event(
            LOGGER,
            logging.WARNING,
            event="llm.request.skipped_tool_call_batch_state_lookup",
            message=(
                "Skipped best-effort tool-call batch shared-state lookup before "
                "event publication"
            ),
            payload=payload,
        )

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
            tool_calls = _tool_calls_from_response(msg)
            if not tool_calls:
                continue
            batch_id = await self._batch_id_for_tool_calls_async(
                request=request,
                tool_calls=tool_calls,
            )
            batch_emitted = False
            for batch_index, part in tool_calls:
                if await self.publish_observed_tool_call_event_async(
                    request=request,
                    part=part,
                    batch_id=batch_id,
                    batch_index=batch_index,
                    batch_size=len(tool_calls),
                    published_tool_call_ids=published_tool_call_ids,
                ):
                    emitted = True
                    batch_emitted = True
            if batch_emitted or await self._tool_call_batch_has_observed_items_async(
                request=request,
                batch_id=batch_id,
            ):
                await self.seal_tool_call_batch_async(
                    request=request,
                    batch_id=batch_id,
                    tool_calls=tool_calls,
                )
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


def _tool_calls_from_response(
    message: ModelResponse,
) -> tuple[tuple[int, ToolCallPart], ...]:
    return tuple(
        (index, part)
        for index, part in enumerate(message.parts)
        if isinstance(part, ToolCallPart)
    )


def _batch_item(index: int, part: ToolCallPart) -> PersistedToolCallBatchItem:
    return PersistedToolCallBatchItem(
        tool_call_id=str(part.tool_call_id or "").strip(),
        tool_name=str(part.tool_name or "").strip(),
        args_preview=_args_preview(cast(object, part.args)),
        index=index,
    )


def _observed_tool_execution_status(
    current: PersistedToolCallState | None,
) -> ToolExecutionStatus:
    if current is None:
        return ToolExecutionStatus.READY
    return current.execution_status


def _batch_items(
    tool_calls: Sequence[tuple[int, ToolCallPart]],
) -> tuple[PersistedToolCallBatchItem, ...]:
    items = tuple(
        _batch_item(index, part)
        for index, part in tool_calls
        if str(part.tool_call_id or "").strip() and str(part.tool_name or "").strip()
    )
    return tuple(sorted(items, key=lambda item: item.index))


def _merge_items(
    current: tuple[PersistedToolCallBatchItem, ...],
    incoming: tuple[PersistedToolCallBatchItem, ...],
) -> tuple[PersistedToolCallBatchItem, ...]:
    merged = {item.tool_call_id: item for item in current}
    for item in incoming:
        merged[item.tool_call_id] = item
    return tuple(sorted(merged.values(), key=lambda item: item.index))


def _args_preview(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
