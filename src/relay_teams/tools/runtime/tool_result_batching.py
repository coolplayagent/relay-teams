# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import Awaitable, Callable

from pydantic import JsonValue

from relay_teams.media import ContentPart
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.json_helpers import (
    normalize_json_value as _normalize_json_value,
)
from relay_teams.tools.runtime.persisted_state import ToolExecutionStatus


_TOOL_RESULT_COMMIT_BUFFER: contextvars.ContextVar["ToolResultCommitBuffer | None"] = (
    contextvars.ContextVar("tool_result_commit_buffer", default=None)
)


class ToolResultCommitItem:
    def __init__(
        self,
        *,
        ctx: ToolContext,
        tool_call_id: str,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        visible_envelope: dict[str, JsonValue],
        internal_data: JsonValue | None,
        runtime_meta: dict[str, JsonValue],
        execution_status: ToolExecutionStatus,
        tool_content_parts: tuple[ContentPart, ...],
        duration_ms: int,
        success: bool,
    ) -> None:
        self.ctx = ctx
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.args_summary = args_summary
        self.visible_envelope = visible_envelope
        self.internal_data = internal_data
        self.runtime_meta = runtime_meta
        self.execution_status = execution_status
        self.tool_content_parts = tool_content_parts
        self.duration_ms = duration_ms
        self.success = success


class ToolBatchActionResult:
    def __init__(
        self,
        *,
        value: object,
        shared: bool,
        wait_ms: int,
    ) -> None:
        self.value = value
        self.shared = shared
        self.wait_ms = wait_ms


class ToolResultCommitBuffer:
    def __init__(self) -> None:
        self._items: list[ToolResultCommitItem] = []
        self._lock = asyncio.Lock()
        self._action_lock = asyncio.Lock()
        self._action_tasks: dict[str, asyncio.Task[object]] = {}
        self._allowed_tools_lock = asyncio.Lock()
        self._allowed_tools_tasks: dict[str, asyncio.Task[tuple[str, ...] | None]] = {}
        self._role_contract_lock = asyncio.Lock()
        self._role_contract_denied_tools: dict[str, tuple[str, ...]] = {}
        self._middleware_bypass_lock = asyncio.Lock()
        self._middleware_bypass_allowed: dict[str, bool] = {}

    async def add_async(self, item: ToolResultCommitItem) -> None:
        async with self._lock:
            self._items.append(item)

    async def pop_items_async(self) -> tuple[ToolResultCommitItem, ...]:
        async with self._lock:
            items = tuple(self._items)
            self._items.clear()
        return items

    async def clone_item_async(
        self,
        *,
        source_tool_call_id: str,
        tool_call_id: str,
        tool_name: str,
        args_summary: dict[str, JsonValue],
        runtime_meta_overrides: dict[str, JsonValue],
    ) -> ToolResultCommitItem | None:
        async with self._lock:
            source = next(
                (
                    item
                    for item in reversed(self._items)
                    if item.tool_call_id == source_tool_call_id
                ),
                None,
            )
            if source is None:
                return None
            runtime_meta = dict(source.runtime_meta)
            runtime_meta.update(runtime_meta_overrides)
            visible_envelope = _clone_json_object(source.visible_envelope)
            meta = visible_envelope.get("meta")
            if isinstance(meta, dict):
                meta.update(runtime_meta)
            else:
                visible_envelope["meta"] = runtime_meta
            item = ToolResultCommitItem(
                ctx=source.ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=visible_envelope,
                internal_data=_clone_json_value(source.internal_data),
                runtime_meta=runtime_meta,
                execution_status=source.execution_status,
                tool_content_parts=source.tool_content_parts,
                duration_ms=0,
                success=source.success,
            )
            self._items.append(item)
            return item

    async def invoke_action_singleflight_async(
        self,
        *,
        key: str,
        factory: Callable[[], Awaitable[object]],
    ) -> ToolBatchActionResult:
        shared = True
        async with self._action_lock:
            task = self._action_tasks.get(key)
            if task is None:
                task = asyncio.create_task(_invoke_action_factory(factory))
                self._action_tasks[key] = task
                shared = False
        wait_started = time.perf_counter()
        value = await task
        wait_ms = int((time.perf_counter() - wait_started) * 1000) if shared else 0
        return ToolBatchActionResult(value=value, shared=shared, wait_ms=wait_ms)

    async def allowed_tools_for_policy_async(
        self,
        *,
        key: str,
        factory: Callable[[], Awaitable[tuple[str, ...] | None]],
    ) -> tuple[str, ...] | None:
        async with self._allowed_tools_lock:
            task = self._allowed_tools_tasks.get(key)
            if task is None:
                task = asyncio.create_task(_invoke_allowed_tools_factory(factory))
                self._allowed_tools_tasks[key] = task
        return await task

    async def role_contract_denied_tools_async(
        self,
        *,
        key: str,
        factory: Callable[[], tuple[str, ...]],
    ) -> tuple[str, ...]:
        async with self._role_contract_lock:
            denied_tools = self._role_contract_denied_tools.get(key)
            if denied_tools is None:
                denied_tools = factory()
                self._role_contract_denied_tools[key] = denied_tools
            return denied_tools

    async def middleware_bypass_allowed_async(
        self,
        *,
        key: str,
        factory: Callable[[], bool],
    ) -> bool:
        async with self._middleware_bypass_lock:
            allowed = self._middleware_bypass_allowed.get(key)
            if allowed is None:
                allowed = factory()
                self._middleware_bypass_allowed[key] = allowed
            return allowed


async def _invoke_action_factory(factory: Callable[[], Awaitable[object]]) -> object:
    return await factory()


async def _invoke_allowed_tools_factory(
    factory: Callable[[], Awaitable[tuple[str, ...] | None]],
) -> tuple[str, ...] | None:
    return await factory()


class tool_result_batch_scope:
    def __init__(self) -> None:
        self.buffer = ToolResultCommitBuffer()
        self._token: contextvars.Token[ToolResultCommitBuffer | None] | None = None

    def __enter__(self) -> ToolResultCommitBuffer:
        self._token = _TOOL_RESULT_COMMIT_BUFFER.set(self.buffer)
        return self.buffer

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None:
        token = self._token
        if token is not None:
            _TOOL_RESULT_COMMIT_BUFFER.reset(token)
        return None


class suspended_tool_result_batching:
    def __init__(self) -> None:
        self._token: contextvars.Token[ToolResultCommitBuffer | None] | None = None

    def __enter__(self) -> None:
        self._token = _TOOL_RESULT_COMMIT_BUFFER.set(None)

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None:
        _ = (exc_type, exc, tb)
        token = self._token
        if token is not None:
            _TOOL_RESULT_COMMIT_BUFFER.reset(token)
        return None


def current_tool_result_commit_buffer() -> ToolResultCommitBuffer | None:
    return _TOOL_RESULT_COMMIT_BUFFER.get()


def _clone_json_object(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    cloned = json.loads(json.dumps(value, ensure_ascii=False))
    if isinstance(cloned, dict):
        return cloned
    return dict(value)


def _clone_json_value(value: JsonValue | None) -> JsonValue | None:
    if value is None:
        return None
    cloned = json.loads(json.dumps(value, ensure_ascii=False))
    return _normalize_json_value(cloned)
