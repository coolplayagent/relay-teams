# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from typing import Any, Protocol, cast

from pydantic import JsonValue

# noinspection PyProtectedMember
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode, build_run_context
from pydantic_ai.messages import (
    ModelRequest,
    ModelRequestPart,
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturn,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import UsageLimits

from relay_teams.logger import get_logger, log_event
from relay_teams.tools.runtime.context import ToolDeps
from relay_teams.tools.runtime.tool_result_batching import (
    ToolResultCommitBuffer,
    current_tool_result_commit_buffer,
)

LOGGER = get_logger(__name__)


TOOL_STEP_BATCH_EXECUTOR_ENABLED_ENV = "RELAY_TEAMS_TOOL_STEP_BATCH_EXECUTOR_ENABLED"
TOOL_STEP_BATCH_CONCURRENCY_ENV = "RELAY_TEAMS_TOOL_STEP_BATCH_CONCURRENCY"
TOOL_STEP_GLOBAL_CONCURRENCY_ENV = "RELAY_TEAMS_TOOL_STEP_GLOBAL_CONCURRENCY"
TOOL_STEP_FLUSH_SIZE_ENV = "RELAY_TEAMS_TOOL_STEP_FLUSH_SIZE"
TOOL_STEP_BATCH_EXECUTOR_ENABLED = True
TOOL_STEP_BATCH_CONCURRENCY = 16
TOOL_STEP_GLOBAL_CONCURRENCY = 64
TOOL_STEP_FLUSH_SIZE = 100
TOOL_STEP_BATCHABLE_TOOLS = frozenset(
    {
        "glob",
        "grep",
        "list_run_tasks",
        "read",
        "todo_read",
    }
)
TOOL_STEP_HISTORY_DEDUP_TOOLS = frozenset(
    {
        "glob",
        "grep",
        "list_run_tasks",
        "read",
        "todo_read",
    }
)


def _resolve_bool_env(name: str, default: bool) -> bool:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    log_event(
        LOGGER,
        logging.WARNING,
        event="session_runtime.invalid_env",
        message="Ignoring invalid boolean session runtime environment override",
        payload={"name": name, "value": raw_value, "default": default},
    )
    return default


def _resolve_positive_int_env(name: str, default: int) -> int:  # pragma: no cover
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = int(raw_value.strip())
    except ValueError:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session_runtime.invalid_env",
            message="Ignoring invalid integer session runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    if value < 1:
        log_event(
            LOGGER,
            logging.WARNING,
            event="session_runtime.invalid_env",
            message="Ignoring non-positive session runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    return value


RELAY_TOOL_STEP_BATCH_EXECUTOR_ENABLED = _resolve_bool_env(
    TOOL_STEP_BATCH_EXECUTOR_ENABLED_ENV,
    TOOL_STEP_BATCH_EXECUTOR_ENABLED,
)
RELAY_TOOL_STEP_BATCH_CONCURRENCY = _resolve_positive_int_env(
    TOOL_STEP_BATCH_CONCURRENCY_ENV,
    TOOL_STEP_BATCH_CONCURRENCY,
)
RELAY_TOOL_STEP_GLOBAL_CONCURRENCY = _resolve_positive_int_env(
    TOOL_STEP_GLOBAL_CONCURRENCY_ENV,
    TOOL_STEP_GLOBAL_CONCURRENCY,
)
RELAY_TOOL_STEP_FLUSH_SIZE = _resolve_positive_int_env(
    TOOL_STEP_FLUSH_SIZE_ENV,
    TOOL_STEP_FLUSH_SIZE,
)
RELAY_TOOL_STEP_GLOBAL_SEMAPHORE = asyncio.Semaphore(RELAY_TOOL_STEP_GLOBAL_CONCURRENCY)


class _InjectionRestartApplied(Exception):
    pass


class AgentRunResult(Protocol):  # pragma: no cover
    @property
    def response(self) -> object:
        raise NotImplementedError

    def new_messages(self) -> Sequence[ModelMessage]:
        raise NotImplementedError

    def usage(self) -> object:
        raise NotImplementedError


class AgentNodeStream(Protocol):  # pragma: no cover
    def __aiter__(self) -> AsyncIterator[object]:
        raise NotImplementedError

    def stream_text(self, *, delta: bool) -> AsyncIterator[str]:
        raise NotImplementedError

    def usage(self) -> object:
        raise NotImplementedError


class AgentNodeStreamContext(Protocol):  # pragma: no cover
    async def __aenter__(self) -> AgentNodeStream:
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None:
        raise NotImplementedError


class StreamableModelRequestNode(Protocol):  # pragma: no cover
    def stream(self, ctx: object) -> AgentNodeStreamContext:
        raise NotImplementedError


class AgentToolEventStream(Protocol):  # pragma: no cover
    def __aiter__(self) -> AsyncIterator[object]:
        raise NotImplementedError  # pragma: no cover


class AgentToolEventStreamContext(Protocol):  # pragma: no cover
    async def __aenter__(self) -> AgentToolEventStream:
        raise NotImplementedError  # pragma: no cover

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None:
        raise NotImplementedError  # pragma: no cover


class StreamableToolCallNode(Protocol):  # pragma: no cover
    @staticmethod
    def stream(ctx: object) -> AgentToolEventStreamContext:
        raise NotImplementedError  # pragma: no cover


class AgentRun(Protocol):  # pragma: no cover
    ctx: object
    result: AgentRunResult | None

    async def __aenter__(self) -> "AgentRun":
        raise NotImplementedError

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> bool | None:
        raise NotImplementedError

    def __aiter__(self) -> "AgentRun":
        raise NotImplementedError

    async def __anext__(self) -> object:
        raise NotImplementedError

    def new_messages(self) -> Sequence[ModelMessage]:
        raise NotImplementedError

    def usage(self) -> object:
        raise NotImplementedError


class CoordinationAgent(Protocol):  # pragma: no cover
    def iter(
        self,
        prompt: str | None,
        *,
        deps: ToolDeps,
        message_history: Sequence[ModelRequest | ModelResponse],
        usage_limits: UsageLimits,
    ) -> AgentRun:
        raise NotImplementedError


class AutoHarnessRuntimeService(Protocol):  # pragma: no cover
    @staticmethod
    def consume_tools_dirty(*, run_id: str, instance_id: str) -> tuple[str, ...]:
        raise NotImplementedError


class RelayToolStepItemResult:
    def __init__(
        self,
        *,
        index: int,
        part: ToolReturnPart,
        user_content: object | None,
    ) -> None:
        self.index = index
        self.part = part
        self.user_content = user_content


class RelayToolStepExecutionResult:
    def __init__(
        self,
        *,
        observed_messages: tuple[ModelRequest, ...],
        output_parts: tuple[ModelRequestPart, ...],
        duration_ms: int,
        tool_count: int,
    ) -> None:
        self.observed_messages = observed_messages
        self.output_parts = output_parts
        self.duration_ms = duration_ms
        self.tool_count = tool_count


class _EmptyToolEventStream:
    def __aiter__(self) -> "_EmptyToolEventStream":
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


def _empty_tool_event_stream() -> AsyncIterator[object]:
    return _EmptyToolEventStream()


def _relay_tool_step_calls(node: CallToolsNode[Any, Any]) -> tuple[ToolCallPart, ...]:
    calls: list[ToolCallPart] = []
    try:
        parts = node.model_response.parts
    except AttributeError:
        return ()
    for part in parts:
        if isinstance(part, ToolCallPart):
            calls.append(part)
            continue
        return ()
    return tuple(calls)


def _relay_tool_step_disabled_reason(node: CallToolsNode[Any, Any]) -> str | None:
    if not RELAY_TOOL_STEP_BATCH_EXECUTOR_ENABLED:
        return "disabled"
    try:
        tool_call_results = node.tool_call_results
        tool_call_metadata = node.tool_call_metadata
    except AttributeError:
        return "unsupported_node"
    if tool_call_results is not None:
        return "deferred_results"
    if tool_call_metadata is not None:
        return "tool_call_metadata"
    calls = _relay_tool_step_calls(node)
    if not calls:
        return "no_batchable_tool_calls"
    for call in calls:
        if call.tool_name not in TOOL_STEP_BATCHABLE_TOOLS:
            return f"non_batchable_tool:{call.tool_name}"
    return None


def _tool_return_part_from_result(
    *,
    call: ToolCallPart,
    tool_result: object,
) -> RelayToolStepItemResult:
    user_content: object | None = None
    metadata: object | None = None
    if isinstance(tool_result, ToolReturn):
        return_value = tool_result.return_value
        user_content = tool_result.content or None
        metadata = tool_result.metadata
    elif isinstance(tool_result, list) and any(
        isinstance(item, ToolReturn) for item in tool_result
    ):
        raise RuntimeError(
            f"Tool {call.tool_name!r} returned nested ToolReturn objects."
        )
    else:
        return_value = tool_result
    return RelayToolStepItemResult(
        index=-1,
        part=ToolReturnPart(
            tool_name=call.tool_name,
            tool_call_id=call.tool_call_id,
            content=cast(Any, return_value),
            metadata=cast(Any, metadata),
        ),
        user_content=user_content,
    )


async def _execute_relay_tool_step_item_async(
    *,
    tool_manager: object,
    call: ToolCallPart,
    validated: object,
    index: int,
) -> RelayToolStepItemResult:
    execute_tool_call = getattr(tool_manager, "execute_tool_call", None)
    if not callable(execute_tool_call):
        raise RuntimeError(
            "Relay tool step manager cannot execute tool calls."
        ) from None
    async with RELAY_TOOL_STEP_GLOBAL_SEMAPHORE:
        result = execute_tool_call(validated)
        if inspect.isawaitable(result):
            result = await result
    item = _tool_return_part_from_result(call=call, tool_result=result)
    item.index = index
    return item


async def _execute_relay_tool_step_bounded_async(
    *,
    tool_manager: object,
    calls: tuple[ToolCallPart, ...],
    validated_calls: tuple[object, ...],
    concurrency: int,
) -> tuple[RelayToolStepItemResult, ...]:
    if len(calls) != len(validated_calls):
        raise RuntimeError("Validated tool call count did not match tool call count.")
    if not calls:
        return ()
    coalesced_results = await _try_execute_relay_tool_step_coalesced_async(
        tool_manager=tool_manager,
        calls=calls,
        validated_calls=validated_calls,
        concurrency=concurrency,
    )
    if coalesced_results is not None:
        return coalesced_results
    limit = max(1, min(concurrency, len(calls)))
    next_index = 0
    pending: dict[asyncio.Task[RelayToolStepItemResult], int] = {}
    results: list[RelayToolStepItemResult | None] = [None] * len(calls)

    def schedule_next() -> None:
        nonlocal next_index
        if next_index >= len(calls):
            return
        index = next_index
        next_index += 1
        item_task = asyncio.create_task(
            _execute_relay_tool_step_item_async(
                tool_manager=tool_manager,
                call=calls[index],
                validated=validated_calls[index],
                index=index,
            ),
            name=f"relay-tool-step:{calls[index].tool_name}",
        )
        pending[item_task] = index

    for _ in range(limit):
        schedule_next()

    try:
        while pending:
            done, _pending = await asyncio.wait(
                set(pending),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for item_task in done:
                _ = pending.pop(item_task)
                result = await item_task
                results[result.index] = result
                schedule_next()
    except BaseException:
        for item_task in pending:
            item_task.cancel()
        raise

    completed: list[RelayToolStepItemResult] = []
    for item in results:
        if item is None:
            raise RuntimeError("Relay tool step completed with a missing result.")
        completed.append(item)
    return tuple(completed)


async def _try_execute_relay_tool_step_coalesced_async(
    *,
    tool_manager: object,
    calls: tuple[ToolCallPart, ...],
    validated_calls: tuple[object, ...],
    concurrency: int,
) -> tuple[RelayToolStepItemResult, ...] | None:
    buffer = current_tool_result_commit_buffer()
    if buffer is None or len(calls) < 2:
        return None
    groups: dict[str, list[int]] = {}
    representative_indices: list[int] = []
    for index, call in enumerate(calls):
        key = _relay_tool_call_coalesce_key(call)
        if key is None:
            return None
        indices = groups.get(key)
        if indices is None:
            groups[key] = [index]
            representative_indices.append(index)
            continue
        indices.append(index)
    if len(representative_indices) == len(calls):
        return None

    representative_calls = tuple(calls[index] for index in representative_indices)
    representative_validated = tuple(
        validated_calls[index] for index in representative_indices
    )
    representative_results = await _execute_relay_tool_step_bounded_raw_async(
        tool_manager=tool_manager,
        calls=representative_calls,
        validated_calls=representative_validated,
        concurrency=concurrency,
    )
    results: list[RelayToolStepItemResult | None] = [None] * len(calls)
    for representative_index, representative_result in zip(
        representative_indices,
        representative_results,
        strict=True,
    ):
        representative_result.index = representative_index
        results[representative_index] = representative_result
        call = calls[representative_index]
        key = _relay_tool_call_coalesce_key(call)
        if key is None:
            raise RuntimeError("Relay tool step coalescing lost a representative key.")
        for duplicate_index in groups[key][1:]:
            duplicate = await _clone_relay_tool_step_result_async(
                buffer=buffer,
                source=representative_result,
                call=calls[duplicate_index],
                index=duplicate_index,
            )
            if duplicate is None:
                raise RuntimeError(
                    "Relay tool step coalescing could not clone a buffered result."
                )
            results[duplicate_index] = duplicate

    completed: list[RelayToolStepItemResult] = []
    for item in results:
        if item is None:
            raise RuntimeError("Relay tool step coalescing produced a missing result.")
        completed.append(item)
    return tuple(completed)


async def _execute_relay_tool_step_bounded_raw_async(
    *,
    tool_manager: object,
    calls: tuple[ToolCallPart, ...],
    validated_calls: tuple[object, ...],
    concurrency: int,
) -> tuple[RelayToolStepItemResult, ...]:
    limit = max(1, min(concurrency, len(calls)))
    next_index = 0
    pending: dict[asyncio.Task[RelayToolStepItemResult], int] = {}
    results: list[RelayToolStepItemResult | None] = [None] * len(calls)

    def schedule_next() -> None:
        nonlocal next_index
        if next_index >= len(calls):
            return
        index = next_index
        next_index += 1
        item_task = asyncio.create_task(
            _execute_relay_tool_step_item_async(
                tool_manager=tool_manager,
                call=calls[index],
                validated=validated_calls[index],
                index=index,
            ),
            name=f"relay-tool-step:{calls[index].tool_name}",
        )
        pending[item_task] = index

    for _ in range(limit):
        schedule_next()

    try:
        while pending:
            done, _pending = await asyncio.wait(
                set(pending),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for item_task in done:
                _ = pending.pop(item_task)
                result = await item_task
                results[result.index] = result
                schedule_next()
    except BaseException:
        for item_task in pending:
            item_task.cancel()
        raise

    completed: list[RelayToolStepItemResult] = []
    for item in results:
        if item is None:
            raise RuntimeError("Relay tool step completed with a missing result.")
        completed.append(item)
    return tuple(completed)


async def _clone_relay_tool_step_result_async(
    *,
    buffer: ToolResultCommitBuffer,
    source: RelayToolStepItemResult,
    call: ToolCallPart,
    index: int,
) -> RelayToolStepItemResult | None:
    source_tool_call_id = str(source.part.tool_call_id or "")
    tool_call_id = str(call.tool_call_id or "")
    clone_item = await buffer.clone_item_async(
        source_tool_call_id=source_tool_call_id,
        tool_call_id=tool_call_id,
        tool_name=call.tool_name,
        args_summary=_relay_tool_call_args_summary(call),
        runtime_meta_overrides={
            "relay_tool_step_coalesced_result": True,
            "tool_action_singleflight_hit": True,
            "tool_action_singleflight_wait_ms": 0,
            "action_duration_ms": 0,
            "duration_ms": 0,
            "total_tool_runtime_ms": 0,
            "tool_batch_wall_ms": 0,
        },
    )
    if clone_item is None:
        return None
    cloned_part = ToolReturnPart(
        tool_name=call.tool_name,
        tool_call_id=call.tool_call_id,
        content=cast(Any, clone_item.visible_envelope),
        metadata=cast(Any, source.part.metadata),
    )
    return RelayToolStepItemResult(
        index=index,
        part=cloned_part,
        user_content=source.user_content,
    )


def _relay_tool_call_coalesce_key(call: ToolCallPart) -> str | None:
    if call.tool_name not in TOOL_STEP_BATCHABLE_TOOLS:
        return None
    return "|".join(
        (call.tool_name, json.dumps(call.args, sort_keys=True, default=str))
    )


def _relay_tool_call_args_summary(call: ToolCallPart) -> dict[str, JsonValue]:
    if isinstance(call.args, dict):
        return {str(key): cast(JsonValue, value) for key, value in call.args.items()}
    return {"args": str(call.args)}


def _relay_tool_step_coalesced_count(calls: tuple[ToolCallPart, ...]) -> int:
    keys = tuple(
        key for key in (_relay_tool_call_coalesce_key(call) for call in calls) if key
    )
    if not keys:
        return 0
    return len(keys) - len(set(keys))


async def _try_execute_relay_tool_step_async(
    *,
    node: CallToolsNode[Any, Any],
    agent_run_ctx: object,
) -> RelayToolStepExecutionResult | None:
    disabled_reason = _relay_tool_step_disabled_reason(node)
    if disabled_reason is not None:
        if disabled_reason != "no_batchable_tool_calls":
            log_event(
                LOGGER,
                logging.DEBUG,
                event="relay_tool_step.fallback",
                message="Relay tool step executor did not handle this batch",
                payload={"reason": disabled_reason},
            )
        return None

    calls = _relay_tool_step_calls(node)
    ctx = cast(Any, agent_run_ctx)
    run_context = build_run_context(ctx)
    run_context = replace(
        run_context,
        retry=ctx.state.retries,
        max_retries=ctx.deps.max_result_retries,
    )
    ctx.deps.tool_manager = await ctx.deps.tool_manager.for_run_step(run_context)
    tool_manager = ctx.deps.tool_manager

    for call in calls:
        tool_def = tool_manager.get_tool_def(call.tool_name)
        if tool_def is None:
            log_event(
                LOGGER,
                logging.DEBUG,
                event="relay_tool_step.fallback",
                message="Relay tool step executor found an unknown tool",
                payload={"tool_name": call.tool_name},
            )
            return None
        if tool_def.kind != "function":
            return None
        if tool_def.sequential:
            return None

    validated_calls: list[object] = []
    try:
        for call in calls:
            validated = await tool_manager.validate_tool_call(call)
            if not validated.args_valid:
                return None
            validated_calls.append(validated)
    except (KeyError, RuntimeError, TypeError, ValueError) as exc:
        log_event(
            LOGGER,
            logging.DEBUG,
            event="relay_tool_step.validation_fallback",
            message="Relay tool step executor fell back after validation failed",
            payload={
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return None

    started = time.perf_counter()
    item_results = await _execute_relay_tool_step_bounded_async(
        tool_manager=tool_manager,
        calls=calls,
        validated_calls=tuple(validated_calls),
        concurrency=RELAY_TOOL_STEP_BATCH_CONCURRENCY,
    )
    item_results, compressed_result_count = _dedupe_tool_returns_for_history(
        item_results
    )
    output_parts: list[ModelRequestPart] = []
    observed_messages: list[ModelRequest] = []
    for item in item_results:
        output_parts.append(item.part)
        observed_messages.append(ModelRequest(parts=[item.part]))
    for item in item_results:
        if item.user_content is not None:
            output_parts.append(UserPromptPart(content=cast(Any, item.user_content)))

    duration_ms = int((time.perf_counter() - started) * 1000)
    cast(Any, node)._events_iterator = _empty_tool_event_stream()
    cast(Any, node)._next_node = ModelRequestNode(ModelRequest(parts=output_parts))
    tool_names: list[JsonValue] = [
        name for name in sorted({call.tool_name for call in calls})
    ]
    event_payload: dict[str, JsonValue] = {
        "tool_count": len(calls),
        "concurrency": RELAY_TOOL_STEP_BATCH_CONCURRENCY,
        "global_concurrency": RELAY_TOOL_STEP_GLOBAL_CONCURRENCY,
        "flush_size": RELAY_TOOL_STEP_FLUSH_SIZE,
        "tool_names": tool_names,
        "relay_tool_step_executor_used": True,
        "relay_tool_step_batch_size": len(calls),
        "relay_tool_step_execute_ms": duration_ms,
        "relay_tool_step_pydantic_bypass_count": len(calls),
        "relay_tool_step_trace_bypass_count": len(calls),
        "relay_tool_step_history_dedup_count": compressed_result_count,
        "relay_tool_step_coalesced_count": _relay_tool_step_coalesced_count(calls),
    }
    log_event(
        LOGGER,
        logging.INFO,
        event="relay_tool_step.executed",
        message="Executed tool step with relay bounded batch executor",
        duration_ms=duration_ms,
        payload=event_payload,
    )
    return RelayToolStepExecutionResult(
        observed_messages=tuple(observed_messages),
        output_parts=tuple(output_parts),
        duration_ms=duration_ms,
        tool_count=len(calls),
    )


def _dedupe_tool_returns_for_history(
    item_results: tuple[RelayToolStepItemResult, ...],
) -> tuple[tuple[RelayToolStepItemResult, ...], int]:
    seen: dict[tuple[str, str], str] = {}
    compressed: list[RelayToolStepItemResult] = []
    compressed_count = 0
    for item in item_results:
        part = item.part
        if part.tool_name not in TOOL_STEP_HISTORY_DEDUP_TOOLS:
            compressed.append(item)
            continue
        fingerprint = _tool_return_content_fingerprint(part.content)
        if fingerprint is None:
            compressed.append(item)
            continue
        key = (part.tool_name, fingerprint)
        first_tool_call_id = seen.get(key)
        if first_tool_call_id is None:
            seen[key] = str(part.tool_call_id or "")
            compressed.append(item)
            continue
        compressed_count += 1
        compressed_part = ToolReturnPart(
            tool_name=part.tool_name,
            tool_call_id=part.tool_call_id,
            content=cast(
                Any,
                {
                    "status": "ok",
                    "content_omitted": True,
                    "duplicate_of_tool_call_id": first_tool_call_id,
                    "message": (
                        "Duplicate tool result omitted from model history; "
                        "use the referenced tool call result."
                    ),
                },
            ),
            metadata=part.metadata,
        )
        compressed.append(
            RelayToolStepItemResult(
                index=item.index,
                part=compressed_part,
                user_content=item.user_content,
            )
        )
    return tuple(compressed), compressed_count


def _tool_return_content_fingerprint(content: object) -> str | None:
    try:
        return json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
