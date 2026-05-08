# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue, ValidationError
from pydantic_ai.messages import ToolReturn

import asyncio
import contextvars
import inspect
import json
import logging
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from hashlib import sha256
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from json import dumps
from typing import (
    Literal,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    overload,
    runtime_checkable,
)
from uuid import uuid4

from relay_teams.logger import get_logger, log_event, log_tool_error
from relay_teams.agent_runtimes.instances.models import (
    AgentRuntimeRecord,
    RuntimeToolSnapshotEntry,
    RuntimeToolsSnapshot,
)
from relay_teams.media import ContentPart, UserPromptContent
from relay_teams.metrics.adapters import record_tool_execution_async
from relay_teams.notifications import NotificationContext, NotificationType
from relay_teams.persistence import is_retryable_sqlite_error
from relay_teams.agents.tasks.task_status_sanitizer import (
    sanitize_task_status_payload,
)
from relay_teams.reminders import ToolResultObservation
from relay_teams.roles.runtime_tools import (
    runtime_denied_tools_for_role,
    runtime_tools_for_role,
)
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.sessions.runs.enums import InjectionSource, RunEventType
from relay_teams.sessions.runs.event_stream import publish_run_event_async
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.sessions.runs.system_injection import SystemInjectionSink

from relay_teams.tools.runtime.approval_ticket_repo import (
    ApprovalTicketRecord,
    ApprovalTicketStatus,
    ApprovalTicketStatusConflictError,
)
from relay_teams.sessions.runs.run_runtime_repo import RunRuntimePhase, RunRuntimeStatus
from relay_teams.trace import trace_span
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.audit import (
    _workspace_file_digest as _workspace_file_digest,
    record_security_audit_event_best_effort_async,
    set_workspace_file_digest_override_for_testing,
)
from relay_teams.tools.runtime.json_helpers import (
    normalize_json_object as _normalize_json_object,
    normalize_json_value as _normalize_json_value,
    safe_json as _safe_json,
    _tool_return_content,
)
from relay_teams.tools.runtime.argument_binding import (
    _bind_tool_action_kwargs,
    _capture_tool_input,
    _resolve_tool_action_annotations,
    _uses_tool_input_dict,
)
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailContext,
    RuntimeGuardrailEvaluation,
    RuntimeGuardrailFinding,
    RuntimeGuardrailPolicy,
    RuntimeGuardrailRuleType,
    RuntimeGuardrailStatus,
    evaluate_in_execution_guardrails,
    evaluate_pre_execution_guardrails,
    guardrail_findings_payload,
    guardrail_meta_status,
    record_runtime_guardrail_findings_async,
    record_runtime_guardrail_tool_call_async,
)
from relay_teams.tools.runtime.models import (
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolError,
    ToolExecutionError,
    ToolInternalRecord,
    ToolRuntimeDecision,
    ToolResultEnvelope,
    ToolResultProjection,
)
from relay_teams.tools.runtime.policy import ToolApprovalPolicy
from relay_teams.tools.runtime.tool_result_batching import (
    ToolResultCommitBuffer,
    ToolResultCommitItem,
    current_tool_result_commit_buffer,
    suspended_tool_result_batching,
    tool_result_batch_scope,
)
from relay_teams.tools.runtime.runtime_phase import (
    _active_subagent_instance_id,
    _finalize_tool_timing_meta,
    _int_meta,
    _running_runtime_phase,
)
from relay_teams.tools.runtime.persisted_state import (
    ToolApprovalMode,
    ToolApprovalStatus,
    ToolExecutionStatus,
    PersistedToolCallState,
    load_tool_call_state_async,
    load_tool_call_states_async,
    merge_tool_call_state_async,
    tool_call_state_mutation,
)
from relay_teams.env.hook_runtime_env import (
    reset_tool_hook_runtime_env,
    set_tool_hook_runtime_env,
)
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookService,
    PermissionDeniedInput,
    PermissionRequestInput,
    PostToolUseFailureInput,
    PostToolUseInput,
    PreToolUseInput,
)
from relay_teams.tools.runtime import action_capacity as _action_capacity
from relay_teams.tools.runtime.action_capacity import (
    invoke_with_tool_action_capacity,
)

LOGGER = get_logger(__name__)
__all__ = (
    "execute_tool",
    "execute_tool_call",
    "flush_tool_result_batch_async",
    "suspended_tool_result_batching",
    "tool_result_batch_scope",
    "tool_result_batching_active",
)
ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")
PER_RUN_TOOL_ACTION_CONCURRENCY = _action_capacity.PER_RUN_TOOL_ACTION_CONCURRENCY
_GLOBAL_TOOL_ACTION_SEMAPHORE = _action_capacity.GLOBAL_TOOL_ACTION_SEMAPHORE
_RUN_TOOL_ACTION_GATES = _action_capacity.RUN_TOOL_ACTION_GATES
_DEFERRED_TOOL_STATE_PERSIST_RETRY_DELAYS_SECONDS = (0.25, 1.0)
_DEFERRED_TOOL_STATE_PERSIST_TASKS: set[asyncio.Task[None]] = set()


async def _record_security_audit_event_best_effort_async(  # pragma: no cover
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> None:
    set_workspace_file_digest_override_for_testing(_workspace_file_digest)
    await record_security_audit_event_best_effort_async(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_input=tool_input,
        visible_envelope=visible_envelope,
        internal_data=internal_data,
        execution_status=execution_status,
    )


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
            event="tool.runtime.invalid_env",
            message="Ignoring invalid tool runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    if value < 1:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.runtime.invalid_env",
            message="Ignoring non-positive tool runtime environment override",
            payload={"name": name, "value": raw_value, "default": default},
        )
        return default
    return value


TOOL_ACTION_WORKER_COUNT = _resolve_positive_int_env(
    "RELAY_TEAMS_TOOL_ACTION_WORKERS",
    64,
)
TOOL_STATE_WORKER_COUNT = _resolve_positive_int_env(
    "RELAY_TEAMS_TOOL_STATE_WORKERS",
    4,
)
TOOL_APPROVAL_WORKER_COUNT = _resolve_positive_int_env(
    "RELAY_TEAMS_TOOL_APPROVAL_WORKERS",
    4,
)
READ_TOOL_STATE_COMPACT_THRESHOLD_BYTES = _resolve_positive_int_env(
    "RELAY_TEAMS_READ_TOOL_STATE_COMPACT_THRESHOLD_BYTES",
    16_384,
)
_TOOL_ACTION_EXECUTOR = ThreadPoolExecutor(
    max_workers=TOOL_ACTION_WORKER_COUNT,
    thread_name_prefix="tool-action",
)
_TOOL_STATE_EXECUTOR = ThreadPoolExecutor(
    max_workers=TOOL_STATE_WORKER_COUNT,
    thread_name_prefix="tool-state",
)
_TOOL_APPROVAL_EXECUTOR = ThreadPoolExecutor(
    max_workers=TOOL_APPROVAL_WORKER_COUNT,
    thread_name_prefix="tool-approval",
)
_BATCH_SINGLEFLIGHT_ACTION_TOOLS = frozenset(
    {
        "glob",
        "grep",
        "list_run_tasks",
        "read",
        "todo_read",
    }
)
_BATCH_DEFERRED_RUNNING_STATE_TOOLS = _BATCH_SINGLEFLIGHT_ACTION_TOOLS | frozenset(
    {"spawn_subagent"}
)
_TOOL_MIDDLEWARE_HOOK_EVENTS = frozenset(
    {
        HookEventName.PRE_TOOL_USE,
        HookEventName.PERMISSION_REQUEST,
        HookEventName.PERMISSION_DENIED,
        HookEventName.POST_TOOL_USE,
        HookEventName.POST_TOOL_USE_FAILURE,
    }
)
TOOL_RESULT_EVENT_PUBLISH_TIMEOUT_SECONDS = 2.0
TOOL_RESULT_STATE_PERSIST_TIMEOUT_SECONDS = 2.0
TOOL_METRICS_RECORD_TIMEOUT_SECONDS = 1.0
TOOL_RESULT_BATCH_FLUSH_TIMEOUT_SECONDS = (
    _resolve_positive_int_env(
        "RELAY_TEAMS_TOOL_RESULT_BATCH_FLUSH_TIMEOUT_MS",
        30_000,
    )
    / 1000
)
TOOL_RESULT_BATCH_MAX_SIZE = _resolve_positive_int_env(
    "RELAY_TEAMS_TOOL_RESULT_BATCH_MAX_SIZE",
    100,
)


async def flush_tool_result_batch_async(  # pragma: no cover
    buffer: ToolResultCommitBuffer,
    *,
    published_tool_outcome_ids: set[str],
) -> bool:
    try:
        items = await asyncio.wait_for(
            buffer.pop_items_async(),
            timeout=TOOL_RESULT_BATCH_FLUSH_TIMEOUT_SECONDS,
        )
        if not items:
            return False
        return await asyncio.wait_for(
            _flush_tool_result_batch_items_async(
                items=items,
                published_tool_outcome_ids=published_tool_outcome_ids,
            ),
            timeout=TOOL_RESULT_BATCH_FLUSH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        log_event(
            LOGGER,
            logging.ERROR,
            event="tool.result_batch.flush_timeout",
            message="Timed out flushing buffered tool results",
            payload={
                "timeout_ms": int(TOOL_RESULT_BATCH_FLUSH_TIMEOUT_SECONDS * 1000),
            },
        )
        raise RuntimeError("tool_result_batch_flush timed out") from exc


async def _flush_tool_result_batch_items_async(
    *,
    items: tuple[ToolResultCommitItem, ...],
    published_tool_outcome_ids: set[str],
) -> bool:
    started = time.perf_counter()
    batch_id = _tool_result_batch_id(items)
    publish_started = time.perf_counter()
    for item in items:
        _mark_tool_result_event_state(
            runtime_meta=item.runtime_meta,
            visible_envelope=item.visible_envelope,
            published=True,
        )
        item.runtime_meta["tool_result_batch_id"] = batch_id
        item.runtime_meta["tool_result_batch_size"] = len(items)
    result_event_ids = await _publish_tool_result_events_batch_async(items)
    for item in items:
        published_tool_outcome_ids.add(item.tool_call_id)
    publish_ms = int((time.perf_counter() - publish_started) * 1000)
    persist_started = time.perf_counter()
    if _can_defer_tool_records_batch(items):
        _defer_tool_records_batch_persist(
            items=items,
            result_event_ids=result_event_ids,
        )
    else:
        await _persist_tool_records_batch_async(
            items=items,
            result_event_ids=result_event_ids,
        )
    persist_ms = int((time.perf_counter() - persist_started) * 1000)
    metrics_ms = 0
    _record_tool_metrics_batch_deferred(items)
    total_ms = int((time.perf_counter() - started) * 1000)
    for item in items:
        item.runtime_meta["tool_result_batch_publish_ms"] = publish_ms
        item.runtime_meta["tool_result_batch_state_persist_ms"] = persist_ms
        item.runtime_meta["tool_result_batch_metrics_ms"] = metrics_ms
        item.runtime_meta["tool_result_batch_total_ms"] = total_ms
        item.runtime_meta["tool_result_publish_ms"] = publish_ms
        item.runtime_meta["tool_result_persist_ms"] = persist_ms
    log_event(
        LOGGER,
        logging.INFO,
        event="tool.result_batch.flushed",
        message="Flushed buffered tool results",
        payload={
            "run_id": items[0].ctx.deps.run_id,
            "task_id": items[0].ctx.deps.task_id,
            "tool_result_batch_id": batch_id,
            "tool_result_batch_size": len(items),
            "tool_result_batch_publish_ms": publish_ms,
            "tool_result_batch_state_persist_ms": persist_ms,
            "tool_result_batch_metrics_ms": metrics_ms,
            "tool_result_batch_total_ms": total_ms,
        },
    )
    return True


def tool_result_batching_active() -> bool:
    return current_tool_result_commit_buffer() is not None


async def _can_use_lightweight_batched_fast_path(
    *,
    ctx: ToolContext,
    tool_name: str,
    approval_request: ToolApprovalRequest | None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None,
    force_approval: bool,
    allow_tool_return: bool,
) -> bool:
    if not (
        tool_result_batching_active()
        and tool_name in _BATCH_SINGLEFLIGHT_ACTION_TOOLS
        and _policy_uses_yolo(ctx.deps.tool_approval_policy)
        and approval_request is None
        and approval_request_factory is None
        and not force_approval
        and allow_tool_return
    ):
        return False
    return await _can_bypass_lightweight_tool_middleware_async(ctx)


async def _can_bypass_lightweight_tool_middleware_async(ctx: ToolContext) -> bool:
    buffer = current_tool_result_commit_buffer()
    if buffer is None:
        return _can_bypass_lightweight_tool_middleware_uncached(ctx)
    key = _tool_middleware_bypass_cache_key(ctx)
    return await buffer.middleware_bypass_allowed_async(
        key=key,
        factory=partial(_can_bypass_lightweight_tool_middleware_uncached, ctx),
    )


def _can_bypass_lightweight_tool_middleware_uncached(ctx: ToolContext) -> bool:
    if _has_tool_middleware_hooks(ctx):
        return False
    guardrail_policy = _guardrail_policy_from_runtime_policy(
        ctx.deps.tool_approval_policy
    )
    return not guardrail_policy.enabled


def _has_tool_middleware_hooks(ctx: ToolContext) -> bool:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return False
    if not isinstance(hook_service, HookService):
        return True
    try:
        snapshot = hook_service.get_effective_config()
    except (OSError, RuntimeError, ValueError):
        return True
    return any(
        bool(snapshot.hooks.get(event_name))
        for event_name in _TOOL_MIDDLEWARE_HOOK_EVENTS
    )


def _defer_batch_tool_recovery_state(*, tool_name: str) -> bool:
    return (
        tool_result_batching_active()
        and tool_name in _BATCH_DEFERRED_RUNNING_STATE_TOOLS
    )


def _can_defer_tool_records_batch(items: tuple[ToolResultCommitItem, ...]) -> bool:
    if not items:
        return False
    return all(item.tool_name in _BATCH_DEFERRED_RUNNING_STATE_TOOLS for item in items)


def _defer_tool_records_batch_persist(
    *,
    items: tuple[ToolResultCommitItem, ...],
    result_event_ids: dict[str, int],
) -> None:
    _ = result_event_ids
    payload: dict[str, JsonValue] = {
        "batch_size": len(items),
        "tool_names": [name for name in sorted({item.tool_name for item in items})],
    }
    log_event(
        LOGGER,
        logging.DEBUG,
        event="tool.result_batch.state_persist_skipped",
        message="Skipped lightweight batched tool result state persist",
        payload=payload,
    )


async def _persist_tool_records_batch_best_effort_async(  # pragma: no cover
    *,
    items: tuple[ToolResultCommitItem, ...],
    result_event_ids: dict[str, int],
) -> None:
    started = time.perf_counter()
    try:
        await _persist_tool_records_batch_async(
            items=items,
            result_event_ids=result_event_ids,
        )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.result_batch.state_persist_deferred_failed",
            message="Deferred tool result state persist failed",
            payload={
                "batch_size": len(items),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return
    duration_ms = int((time.perf_counter() - started) * 1000)
    if duration_ms >= 1000:
        log_event(
            LOGGER,
            logging.DEBUG,
            event="tool.result_batch.state_persist_deferred",
            message="Deferred tool result state persist completed",
            duration_ms=duration_ms,
            payload={"batch_size": len(items)},
        )


async def _run_tool_state_work(  # pragma: no cover
    function: Callable[ParamT, ResultT],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _TOOL_STATE_EXECUTOR,
        partial(function, *args, **kwargs),
    )


async def _run_tool_approval_work(  # pragma: no cover
    function: Callable[ParamT, ResultT],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _TOOL_APPROVAL_EXECUTOR,
        partial(function, *args, **kwargs),
    )


@runtime_checkable
class _AsyncToolResultReminderService(Protocol):
    async def observe_tool_result_async(
        self, observation: ToolResultObservation
    ) -> object:
        pass


@runtime_checkable
class _AsyncRunRuntimeRepository(Protocol):
    async def ensure_async(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str | None = None,
        status: RunRuntimeStatus = RunRuntimeStatus.QUEUED,
        phase: RunRuntimePhase = RunRuntimePhase.IDLE,
    ) -> object:
        pass

    @staticmethod
    async def update_async(run_id: str, **changes: object) -> object:
        pass


@runtime_checkable
class _AsyncRunEventBatchPublisher(Protocol):
    async def publish_many_async(
        self,
        events: tuple[RunEvent, ...],
    ) -> tuple[int, ...]:
        raise NotImplementedError


@runtime_checkable
class _AsyncRunEventDeferredBatchPublisher(Protocol):
    async def publish_many_deferred_async(
        self,
        events: tuple[RunEvent, ...],
    ) -> tuple[int, ...]:
        raise NotImplementedError


@runtime_checkable
class _RuntimeToolsAgentRepository(Protocol):
    @staticmethod
    async def get_instance_async(instance_id: str) -> AgentRuntimeRecord:
        raise NotImplementedError


# noinspection PyUnusedLocal,PyTypeHints
@overload
async def execute_tool(  # pragma: no cover
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[[dict[str, JsonValue]], object | Awaitable[object]]
    | Callable[[], object | Awaitable[object]]
    | object,
    tool_input: dict[str, JsonValue] | None = None,
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: Literal[False] = False,
) -> dict[str, JsonValue]: ...


# noinspection PyUnusedLocal,PyTypeHints
@overload
async def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[[dict[str, JsonValue]], object | Awaitable[object]]
    | Callable[[], object | Awaitable[object]]
    | object,
    tool_input: dict[str, JsonValue] | None = None,
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: Literal[True] = True,
) -> ToolReturn | dict[str, JsonValue]: ...


# noinspection PyUnusedLocal,PyTypeHints,PyRedeclaration
async def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[[dict[str, JsonValue]], object | Awaitable[object]]
    | Callable[[], object | Awaitable[object]]
    | object,
    tool_input: dict[str, JsonValue] | None = None,
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: bool = False,
) -> ToolReturn | dict[str, JsonValue]:
    """Run a tool action with approval, logging, and normalized envelopes."""
    tool_call_id = ctx.tool_call_id or f"toolcall_{uuid4().hex[:12]}"
    with trace_span(
        LOGGER,
        component="tools.runtime",
        operation="execute_tool",
        attributes={"tool_name": tool_name},
        trace_id=ctx.deps.trace_id,
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        session_id=ctx.deps.session_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_call_id=tool_call_id,
    ):
        started = time.perf_counter()
        log_event(
            LOGGER,
            logging.DEBUG,
            event="tool.call.started",
            message="Tool call started",
            payload={
                "tool_name": tool_name,
                "args": args_summary,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )
        meta: dict[str, JsonValue] = {}
        effective_tool_input = dict(args_summary if tool_input is None else tool_input)
        _raise_if_stopped(ctx)
        role_contract_error = await _apply_role_contract_check_async(
            ctx=ctx,
            tool_name=tool_name,
        )
        if role_contract_error is not None:
            elapsed_ms = _finalize_tool_timing_meta(
                runtime_meta=meta,
                started=started,
            )
            meta["approval_status"] = "denied_by_policy"
            meta["runtime_policy_decision"] = "deny"
            meta["role_id"] = ctx.deps.role_id
            meta["tool_name"] = tool_name
            envelope = _visible_envelope(
                ok=False,
                error=role_contract_error,
                meta=meta,
            )
            await _observe_tool_result_reminders_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                envelope=envelope,
            )
            await _persist_and_publish_tool_result_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            await _record_tool_metrics_async(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            return envelope
        requested_force_approval = force_approval
        if await _can_use_lightweight_batched_fast_path(
            ctx=ctx,
            tool_name=tool_name,
            approval_request=approval_request,
            approval_request_factory=approval_request_factory,
            force_approval=requested_force_approval,
            allow_tool_return=allow_tool_return,
        ):
            try:
                result = await _invoke_tool_action_with_limits(
                    ctx=ctx,
                    tool_name=tool_name,
                    action=action,
                    tool_input=effective_tool_input,
                    runtime_meta=meta,
                    hold_action_capacity=False,
                )
                (
                    visible_data,
                    internal_data,
                    tool_content_parts,
                ) = _normalize_result_payload(result)
                fast_tool_return_content: UserPromptContent | None = None
                if tool_content_parts and not allow_tool_return:
                    raise ValueError(
                        f"Tool {tool_name} produced model content on the lightweight batch fast path."
                    )
                if tool_content_parts:
                    fast_tool_return_content = _tool_return_content(
                        ctx=ctx,
                        tool_name=tool_name,
                        tool_content_parts=tool_content_parts,
                    )
                _ = _finalize_tool_timing_meta(runtime_meta=meta, started=started)
                meta["run_yolo"] = True
                meta["approval_required"] = False
                meta["approval_mode"] = ToolApprovalMode.YOLO.value
                meta["approval_status"] = "not_required"
                meta["lightweight_batch_fast_path"] = True
                envelope = _visible_envelope(ok=True, data=visible_data, meta=meta)
                await _persist_and_publish_tool_result_async(
                    ctx=ctx,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args_summary=args_summary,
                    visible_envelope=envelope,
                    internal_data=internal_data,
                    runtime_meta=meta,
                    execution_status=ToolExecutionStatus.COMPLETED,
                    tool_content_parts=tool_content_parts,
                )
                await _record_security_audit_event_best_effort_async(
                    ctx=ctx,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_input=effective_tool_input,
                    visible_envelope=envelope,
                    internal_data=internal_data,
                    execution_status=ToolExecutionStatus.COMPLETED,
                )
                if fast_tool_return_content is not None:
                    return ToolReturn(
                        return_value=envelope,
                        content=fast_tool_return_content,
                    )
                return envelope
            except Exception as exc:
                elapsed_ms = _finalize_tool_timing_meta(
                    runtime_meta=meta,
                    started=started,
                )
                error = _error_payload(exc)
                if error.details:
                    meta["error_details"] = dict(error.details)
                meta["run_yolo"] = True
                meta["approval_required"] = False
                meta["approval_mode"] = ToolApprovalMode.YOLO.value
                meta["approval_status"] = "not_required"
                meta["lightweight_batch_fast_path"] = True
                envelope = _visible_envelope(ok=False, error=error, meta=meta)
                await _observe_tool_result_reminders_async(
                    ctx=ctx,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    envelope=envelope,
                )
                await _record_security_audit_event_best_effort_async(
                    ctx=ctx,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tool_input=effective_tool_input,
                    visible_envelope=envelope,
                    internal_data=None,
                    execution_status=ToolExecutionStatus.FAILED,
                )
                await _persist_and_publish_tool_result_async(
                    ctx=ctx,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args_summary=args_summary,
                    visible_envelope=envelope,
                    internal_data=None,
                    runtime_meta=meta,
                    execution_status=ToolExecutionStatus.FAILED,
                )
                await _record_tool_metrics_async(
                    ctx=ctx,
                    tool_name=tool_name,
                    duration_ms=elapsed_ms,
                    success=False,
                )
                return envelope
        hook_force_approval = False
        (
            effective_tool_input,
            pre_tool_error,
            hook_force_approval,
        ) = await _apply_pre_tool_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=effective_tool_input,
        )
        args_summary = dict(effective_tool_input)
        pre_guardrail_error = await _apply_pre_execution_guardrails(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=effective_tool_input,
            meta=meta,
        )
        if pre_tool_error is None and pre_guardrail_error is not None:
            pre_tool_error = pre_guardrail_error
        defer_recovery_state = _defer_batch_tool_recovery_state(tool_name=tool_name)
        reusable_result = None
        if not defer_recovery_state:
            reusable_result = await _reusable_tool_result_async(
                ctx=ctx,
                args_preview=_safe_json(args_summary),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                allow_tool_return=allow_tool_return,
            )
        if pre_tool_error is None and reusable_result is not None:
            log_event(
                LOGGER,
                logging.INFO,
                event="tool.call.reused_result",
                message="Reused persisted tool result for duplicate tool call",
                payload={
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "instance_id": ctx.deps.instance_id,
                    "role_id": ctx.deps.role_id,
                },
            )
            return reusable_result
        resolved_approval_request = (
            approval_request_factory(effective_tool_input)
            if approval_request_factory is not None
            else approval_request
        )
        resolved_approval_args_summary = (
            approval_args_summary_factory(effective_tool_input)
            if approval_args_summary_factory is not None
            else approval_args_summary
        )
        if pre_tool_error is not None:
            approval_ticket_id = None
            approval_error = pre_tool_error
        else:
            approval_ticket_id, approval_error = await _handle_tool_approval(
                ctx=ctx,
                tool_name=tool_name,
                args_summary=args_summary,
                approval_args_summary=resolved_approval_args_summary,
                meta=meta,
                tool_call_id=tool_call_id,
                approval_request=resolved_approval_request,
                force_approval=requested_force_approval or hook_force_approval,
            )
        if approval_error is not None:
            elapsed_ms = _finalize_tool_timing_meta(
                runtime_meta=meta,
                started=started,
            )
            envelope = _visible_envelope(
                ok=False,
                error=approval_error,
                meta=meta,
            )
            await _observe_tool_result_reminders_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                envelope=envelope,
            )
            await _record_security_audit_event_best_effort_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=effective_tool_input,
                visible_envelope=envelope,
                internal_data=None,
                execution_status=ToolExecutionStatus.FAILED,
            )
            await _persist_and_publish_tool_result_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            await _record_tool_metrics_async(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            return envelope

        if defer_recovery_state:
            meta["tool_running_state_deferred"] = True
        else:
            await _ensure_run_runtime_async(ctx=ctx)
            await _update_run_runtime_async(
                ctx=ctx,
                status=RunRuntimeStatus.RUNNING,
                phase=_running_runtime_phase(ctx),
                active_instance_id=ctx.deps.instance_id,
                active_task_id=ctx.deps.task_id,
                active_role_id=ctx.deps.role_id,
                active_subagent_instance_id=_active_subagent_instance_id(ctx),
                last_error=None,
            )
            try:
                await _mark_tool_running_async(
                    ctx=ctx,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args_summary=args_summary,
                    runtime_meta=meta,
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    event="tool.running_state_persist_failed",
                    message=(
                        "Tool call will run, but its pre-run recovery state could "
                        "not be persisted"
                    ),
                    payload={
                        "run_id": ctx.deps.run_id,
                        "task_id": ctx.deps.task_id,
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

        try:
            _raise_if_stopped(ctx)
            hook_env_token = set_tool_hook_runtime_env(ctx.deps.hook_runtime_env)
            try:
                result = await _invoke_tool_action_with_limits(
                    ctx=ctx,
                    tool_name=tool_name,
                    action=action,
                    tool_input=effective_tool_input,
                    runtime_meta=meta,
                    hold_action_capacity=hold_action_capacity,
                )
            finally:
                reset_tool_hook_runtime_env(hook_env_token)
            _raise_if_stopped(ctx)
            visible_data, internal_data, tool_content_parts = _normalize_result_payload(
                result
            )

            elapsed_ms = _finalize_tool_timing_meta(
                runtime_meta=meta,
                started=started,
            )

            log_event(
                LOGGER,
                logging.DEBUG,
                event="tool.call.completed",
                message="Tool call completed",
                duration_ms=elapsed_ms,
                payload={"tool_name": tool_name},
            )

            envelope = _visible_envelope(
                ok=True,
                data=visible_data,
                meta=meta,
            )
            tool_return_content: UserPromptContent | None = None
            if tool_content_parts:
                if not allow_tool_return:
                    raise ValueError(
                        f"Tool {tool_name} produced model content without enabling tool returns."
                    )
                tool_return_content = _tool_return_content(
                    ctx=ctx,
                    tool_name=tool_name,
                    tool_content_parts=tool_content_parts,
                )

            envelope = await _apply_post_tool_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args_summary=args_summary,
                envelope=envelope,
            )
            envelope = await _apply_in_execution_guardrails(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=effective_tool_input,
                envelope=envelope,
            )
            final_success = not _visible_tool_result_is_error(envelope)
            execution_status = (
                ToolExecutionStatus.COMPLETED
                if final_success
                else ToolExecutionStatus.FAILED
            )
            await _observe_tool_result_reminders_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                envelope=envelope,
            )
            await _persist_and_publish_tool_result_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=internal_data if final_success else None,
                runtime_meta=meta,
                execution_status=execution_status,
                tool_content_parts=tool_content_parts if final_success else (),
            )
            await _record_tool_metrics_async(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=_int_meta(meta, "duration_ms"),
                success=final_success,
            )
            if approval_ticket_id and not keep_approval_ticket_reusable:
                await ctx.deps.approval_ticket_repo.mark_completed_async(
                    approval_ticket_id
                )
            await _record_security_audit_event_best_effort_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=effective_tool_input,
                visible_envelope=envelope,
                internal_data=internal_data,
                execution_status=execution_status,
            )
            if final_success and tool_return_content is not None:
                return ToolReturn(
                    return_value=envelope,
                    content=tool_return_content,
                )
            return envelope
        except Exception as exc:
            elapsed_ms = _finalize_tool_timing_meta(
                runtime_meta=meta,
                started=started,
            )
            error = _error_payload(exc)
            if error.details:
                meta["error_details"] = dict(error.details)

            compact = json.dumps(
                {
                    "tool": tool_name,
                    "type": error.type,
                    "message": error.message,
                    "details": error.details,
                },
                ensure_ascii=False,
            )
            log_tool_error(ctx.deps.role_id, compact)
            log_event(
                LOGGER,
                logging.ERROR,
                event="tool.call.failed",
                message="Tool call failed",
                duration_ms=elapsed_ms,
                payload={
                    "tool_name": tool_name,
                    "error_type": error.type,
                    "retryable": error.retryable,
                    "details": error.details,
                },
            )
            envelope = _visible_envelope(
                ok=False,
                error=error,
                meta=meta,
            )
            envelope = await _apply_post_tool_failure_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                args_summary=args_summary,
                envelope=envelope,
            )
            envelope = await _apply_in_execution_guardrails(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=effective_tool_input,
                envelope=envelope,
            )
            await _observe_tool_result_reminders_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                envelope=envelope,
            )
            await _record_security_audit_event_best_effort_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=effective_tool_input,
                visible_envelope=envelope,
                internal_data=None,
                execution_status=ToolExecutionStatus.FAILED,
            )
            await _persist_and_publish_tool_result_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            await _record_tool_metrics_async(
                ctx=ctx,
                tool_name=tool_name,
                duration_ms=elapsed_ms,
                success=False,
            )
            if approval_ticket_id and not keep_approval_ticket_reusable:
                await ctx.deps.approval_ticket_repo.mark_completed_async(
                    approval_ticket_id
                )
            return envelope


# noinspection PyUnusedLocal,PyTypeHints
@overload
async def execute_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[..., object | Awaitable[object]] | object,
    raw_args: Mapping[str, object] | None = None,
    args_exclude: tuple[str, ...] = ("ctx",),
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: Literal[False] = False,
) -> dict[str, JsonValue]: ...


# noinspection PyUnusedLocal,PyTypeHints
@overload
async def execute_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[..., object | Awaitable[object]] | object,
    raw_args: Mapping[str, object] | None = None,
    args_exclude: tuple[str, ...] = ("ctx",),
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: Literal[True] = True,
) -> ToolReturn | dict[str, JsonValue]: ...


# noinspection PyUnusedLocal,PyTypeHints,PyRedeclaration
async def execute_tool_call(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[..., object | Awaitable[object]] | object,
    raw_args: Mapping[str, object] | None = None,
    args_exclude: tuple[str, ...] = ("ctx",),
    approval_request: ToolApprovalRequest | None = None,
    approval_request_factory: Callable[
        [dict[str, JsonValue]], ToolApprovalRequest | None
    ]
    | None = None,
    approval_args_summary: dict[str, JsonValue] | None = None,
    approval_args_summary_factory: Callable[
        [dict[str, JsonValue]], dict[str, JsonValue] | None
    ]
    | None = None,
    keep_approval_ticket_reusable: bool = False,
    force_approval: bool = False,
    hold_action_capacity: bool = True,
    allow_tool_return: bool = False,
) -> ToolReturn | dict[str, JsonValue]:
    """Run a tool through the hook-aware runtime using natural Python params.

    Tool authors should prefer this wrapper for new tools:
    - keep ``action`` as a normal callable with named parameters that match the tool
    - pass ``raw_args=locals()`` from the tool body so hooks can rewrite the live input
    - keep ``args_summary`` limited to approval and observability data, not full payloads

    ``execute_tool()`` remains available for compatibility, but ``execute_tool_call()``
    is the default authoring path because it centralizes hook input capture, argument
    binding, and runtime env propagation.
    """
    tool_input = (
        None
        if raw_args is None
        else _capture_tool_input(
            raw_args=raw_args,
            action=action,
            exclude=args_exclude,
        )
    )
    if allow_tool_return:
        return await execute_tool(
            ctx,
            tool_name=tool_name,
            args_summary=args_summary,
            action=action,
            tool_input=tool_input,
            approval_request=approval_request,
            approval_request_factory=approval_request_factory,
            approval_args_summary=approval_args_summary,
            approval_args_summary_factory=approval_args_summary_factory,
            keep_approval_ticket_reusable=keep_approval_ticket_reusable,
            force_approval=force_approval,
            hold_action_capacity=hold_action_capacity,
            allow_tool_return=True,
        )
    return await execute_tool(
        ctx,
        tool_name=tool_name,
        args_summary=args_summary,
        action=action,
        tool_input=tool_input,
        approval_request=approval_request,
        approval_request_factory=approval_request_factory,
        approval_args_summary=approval_args_summary,
        approval_args_summary_factory=approval_args_summary_factory,
        keep_approval_ticket_reusable=keep_approval_ticket_reusable,
        force_approval=force_approval,
        hold_action_capacity=hold_action_capacity,
        allow_tool_return=False,
    )


async def _reusable_tool_result_async(  # pragma: no cover
    *,
    ctx: ToolContext,
    args_preview: str,
    tool_call_id: str,
    tool_name: str,
    allow_tool_return: bool,
) -> (ToolReturn | dict[str, JsonValue]) | None:
    state = await load_tool_call_state_async(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
    )
    if state is None or state.tool_name != tool_name:
        return None
    if state.args_preview != args_preview:
        return None
    if not _state_matches_runtime_scope(ctx=ctx, state_run_id=state.run_id):
        return None
    if state.execution_status not in {
        ToolExecutionStatus.COMPLETED,
        ToolExecutionStatus.FAILED,
    }:
        return None
    result_envelope = state.result_envelope
    if not isinstance(result_envelope, dict):
        return None
    try:
        record = ToolInternalRecord.model_validate(result_envelope)
    except ValidationError:
        record = None
    if record is not None:
        visible_result = _normalize_json_object(
            record.visible_result.model_dump(mode="json")
        )
        if record.tool_content_parts and allow_tool_return:
            try:
                tool_return_content = _tool_return_content(
                    ctx=ctx,
                    tool_name=tool_name,
                    tool_content_parts=tuple(record.tool_content_parts),
                )
            except Exception as exc:
                return _visible_envelope(
                    ok=False,
                    error=_error_payload(exc),
                    meta={"reused_tool_call": True},
                )
            return ToolReturn(
                return_value=visible_result,
                content=tool_return_content,
            )
        return visible_result
    visible_result = result_envelope.get("visible_result")
    if isinstance(visible_result, dict):
        return _normalize_json_object(visible_result)
    return _normalize_json_object(result_envelope)


def _state_matches_runtime_scope(
    *,
    ctx: ToolContext,
    state_run_id: str,
) -> bool:
    return not state_run_id or state_run_id == ctx.deps.run_id


async def _record_tool_metrics_async(  # pragma: no cover
    *,
    ctx: ToolContext,
    tool_name: str,
    duration_ms: int,
    success: bool,
) -> None:
    if current_tool_result_commit_buffer() is not None:
        return
    metric_recorder = getattr(ctx.deps, "metric_recorder", None)
    mcp_registry = getattr(ctx.deps, "mcp_registry", None)
    if metric_recorder is None or mcp_registry is None:
        return
    try:
        await asyncio.wait_for(
            record_tool_execution_async(
                metric_recorder,
                mcp_registry=mcp_registry,
                workspace_id=ctx.deps.workspace_id,
                session_id=ctx.deps.session_id,
                run_id=ctx.deps.run_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=success,
            ),
            timeout=TOOL_METRICS_RECORD_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, RuntimeError, sqlite3.Error) as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.metrics.record_deferred",
            message="Tool metrics write skipped on tool hot path",
            payload={
                "tool_name": tool_name,
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "error_type": type(exc).__name__,
            },
        )


async def _ensure_run_runtime_async(  # pragma: no cover
    *,
    ctx: ToolContext,
    status: RunRuntimeStatus = RunRuntimeStatus.QUEUED,
    phase: RunRuntimePhase = RunRuntimePhase.IDLE,
) -> None:
    repository = ctx.deps.run_runtime_repo
    if isinstance(repository, _AsyncRunRuntimeRepository):
        _ = await repository.ensure_async(
            run_id=ctx.deps.run_id,
            session_id=ctx.deps.session_id,
            root_task_id=ctx.deps.task_id,
            status=status,
            phase=phase,
        )
        return
    ensure_kwargs: dict[str, object] = {
        "run_id": ctx.deps.run_id,
        "session_id": ctx.deps.session_id,
        "root_task_id": ctx.deps.task_id,
    }
    if status != RunRuntimeStatus.QUEUED:
        ensure_kwargs["status"] = status
    if phase != RunRuntimePhase.IDLE:
        ensure_kwargs["phase"] = phase
    _ = await _run_tool_state_work(repository.ensure, **ensure_kwargs)


async def _update_run_runtime_async(
    *,
    ctx: ToolContext,
    **changes: object,
) -> None:
    repository = ctx.deps.run_runtime_repo
    if isinstance(repository, _AsyncRunRuntimeRepository):
        _ = await repository.update_async(ctx.deps.run_id, **changes)
        return
    _ = await _run_tool_state_work(repository.update, ctx.deps.run_id, **changes)


async def _publish_tool_result_event_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
) -> int:
    return await publish_run_event_async(
        ctx.deps.run_event_hub,
        _tool_result_run_event(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            visible_envelope=visible_envelope,
        ),
    )


async def _publish_tool_result_events_batch_async(  # pragma: no cover
    items: tuple[ToolResultCommitItem, ...],
) -> dict[str, int]:
    if not items:
        return {}
    events = tuple(
        _tool_result_run_event(
            ctx=item.ctx,
            tool_call_id=item.tool_call_id,
            tool_name=item.tool_name,
            visible_envelope=item.visible_envelope,
        )
        for item in items
    )
    hub = items[0].ctx.deps.run_event_hub
    if _can_defer_tool_records_batch(items) and isinstance(
        hub,
        _AsyncRunEventDeferredBatchPublisher,
    ):
        event_ids = await hub.publish_many_deferred_async(events)
    elif isinstance(hub, _AsyncRunEventBatchPublisher):
        event_ids = await hub.publish_many_async(events)
    else:
        event_ids = tuple(
            [
                await publish_run_event_async(item.ctx.deps.run_event_hub, event)
                for item, event in zip(items, events, strict=True)
            ]
        )
    return {
        item.tool_call_id: event_id
        for item, event_id in zip(items, event_ids, strict=True)
    }


def _tool_result_run_event(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
) -> RunEvent:
    result_payload = cast(
        JsonValue,
        sanitize_task_status_payload(visible_envelope),
    )
    is_error = _visible_tool_result_is_error(visible_envelope)
    metrics = _tool_result_metrics_from_visible_envelope(visible_envelope)
    return RunEvent(
        session_id=ctx.deps.session_id,
        run_id=ctx.deps.run_id,
        trace_id=ctx.deps.trace_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        event_type=RunEventType.TOOL_RESULT,
        payload_json=dumps(
            {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "result": result_payload,
                "error": is_error,
                "role_id": ctx.deps.role_id,
                "instance_id": ctx.deps.instance_id,
                "metrics": metrics,
            }
        ),
    )


def _tool_result_metrics_from_visible_envelope(
    visible_envelope: dict[str, JsonValue],
) -> dict[str, int]:
    raw_meta = visible_envelope.get("meta")
    if not isinstance(raw_meta, dict):
        return {}
    meta = cast(dict[str, JsonValue], raw_meta)
    metrics: dict[str, int] = {}
    for key in (
        "action_queue_wait_ms",
        "action_duration_ms",
        "tool_framework_wait_ms",
        "tool_result_persist_ms",
        "tool_result_publish_ms",
        "tool_batch_wall_ms",
        "state_persist_ms",
        "event_publish_ms",
        "total_tool_runtime_ms",
        "tool_result_batch_size",
        "tool_result_batch_publish_ms",
        "tool_result_batch_state_persist_ms",
        "tool_result_batch_metrics_ms",
        "tool_result_batch_total_ms",
        "tool_action_singleflight_wait_ms",
    ):
        value = meta.get(key)
        if type(value) is int:
            metrics[key] = value
    return metrics


def _mark_tool_result_event_state(  # pragma: no cover
    *,
    runtime_meta: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    published: bool,
) -> None:
    runtime_meta["tool_result_durably_recorded"] = True
    runtime_meta["tool_result_event_published"] = published
    raw_meta = visible_envelope.get("meta")
    envelope_meta = (
        _normalize_json_object(raw_meta) if isinstance(raw_meta, dict) else {}
    )
    envelope_meta["tool_result_durably_recorded"] = True
    envelope_meta["tool_result_event_published"] = published
    visible_envelope["meta"] = envelope_meta


def _visible_tool_result_is_error(visible_envelope: dict[str, JsonValue]) -> bool:
    if visible_envelope.get("ok") is False:
        return True
    data = visible_envelope.get("data")
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if isinstance(status, str) and status.strip().lower() in {"failed", "error"}:
        return True
    exit_code = data.get("exit_code")
    return type(exit_code) is int and exit_code != 0


async def _persist_and_publish_tool_result_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
    tool_content_parts: tuple[ContentPart, ...] = (),
) -> None:
    buffer = current_tool_result_commit_buffer()
    if buffer is not None:
        await buffer.add_async(
            ToolResultCommitItem(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=dict(args_summary),
                visible_envelope=visible_envelope,
                internal_data=internal_data,
                runtime_meta=runtime_meta,
                execution_status=execution_status,
                tool_content_parts=tool_content_parts,
                duration_ms=_int_meta(runtime_meta, "duration_ms"),
                success=execution_status == ToolExecutionStatus.COMPLETED,
            )
        )
        return
    _mark_tool_result_event_state(
        runtime_meta=runtime_meta,
        visible_envelope=visible_envelope,
        published=True,
    )
    event_started = time.perf_counter()
    try:
        result_event_id = await asyncio.wait_for(
            _publish_tool_result_event_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                visible_envelope=visible_envelope,
            ),
            timeout=TOOL_RESULT_EVENT_PUBLISH_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        event_publish_ms = int((time.perf_counter() - event_started) * 1000)
        runtime_meta["event_publish_ms"] = event_publish_ms
        runtime_meta["tool_result_publish_ms"] = event_publish_ms
        _mark_tool_result_event_state(
            runtime_meta=runtime_meta,
            visible_envelope=visible_envelope,
            published=False,
        )
        _ = await _persist_tool_record_best_effort_async(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_summary=args_summary,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            runtime_meta=runtime_meta,
            execution_status=execution_status,
            tool_content_parts=tool_content_parts,
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.result_event_publish_failed",
            message=(
                "Tool result state was persisted without a result event because "
                "event publishing failed"
            ),
            payload={
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return
    event_publish_ms = int((time.perf_counter() - event_started) * 1000)
    runtime_meta["event_publish_ms"] = event_publish_ms
    runtime_meta["tool_result_publish_ms"] = event_publish_ms
    _ = await _persist_tool_record_best_effort_async(
        ctx=ctx,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        args_summary=args_summary,
        visible_envelope=visible_envelope,
        internal_data=internal_data,
        runtime_meta=runtime_meta,
        execution_status=execution_status,
        tool_content_parts=tool_content_parts,
        result_event_id=result_event_id,
    )


async def _persist_tool_record_best_effort_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
    tool_content_parts: tuple[ContentPart, ...] = (),
    result_event_id: int = 0,
) -> bool:
    state_started = time.perf_counter()
    try:
        await asyncio.wait_for(
            _persist_tool_record_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=visible_envelope,
                internal_data=internal_data,
                runtime_meta=runtime_meta,
                execution_status=execution_status,
                tool_content_parts=tool_content_parts,
                result_event_id=result_event_id,
            ),
            timeout=TOOL_RESULT_STATE_PERSIST_TIMEOUT_SECONDS,
        )
        return True
    except (asyncio.TimeoutError, RuntimeError, sqlite3.Error) as exc:
        _schedule_deferred_tool_record_persist(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_summary=args_summary,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            runtime_meta=runtime_meta,
            execution_status=execution_status,
            tool_content_parts=tool_content_parts,
            result_event_id=result_event_id,
        )
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.result_state_persist_deferred",
            message="Tool result state write deferred from tool hot path",
            payload={
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "result_event_id": result_event_id,
                "error_type": type(exc).__name__,
            },
        )
        return False
    finally:
        state_persist_ms = int((time.perf_counter() - state_started) * 1000)
        runtime_meta["state_persist_ms"] = state_persist_ms
        runtime_meta["tool_result_persist_ms"] = state_persist_ms


def _schedule_deferred_tool_record_persist(  # pragma: no cover
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
    result_event_id: int,
) -> None:
    task = asyncio.create_task(
        _persist_tool_record_deferred_async(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_summary=args_summary,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            runtime_meta=runtime_meta,
            execution_status=execution_status,
            tool_content_parts=tool_content_parts,
            result_event_id=result_event_id,
        )
    )
    _DEFERRED_TOOL_STATE_PERSIST_TASKS.add(task)
    task.add_done_callback(_discard_deferred_tool_state_persist_task)


def _discard_deferred_tool_state_persist_task(  # pragma: no cover
    task: asyncio.Task[None],
) -> None:
    _DEFERRED_TOOL_STATE_PERSIST_TASKS.discard(task)
    if task.cancelled():
        return
    try:
        task.result()
    except Exception as exc:
        log_event(
            LOGGER,
            logging.ERROR,
            event="tool.result_state_persist_deferred_failed",
            message="Deferred tool result state write failed",
            exc_info=exc,
        )


async def _persist_tool_record_deferred_async(  # pragma: no cover
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
    result_event_id: int,
) -> None:
    for attempt, delay_seconds in enumerate(
        (0.0, *_DEFERRED_TOOL_STATE_PERSIST_RETRY_DELAYS_SECONDS),
        start=1,
    ):
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)
        try:
            await _persist_tool_record_async(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=visible_envelope,
                internal_data=internal_data,
                runtime_meta=runtime_meta,
                execution_status=execution_status,
                tool_content_parts=tool_content_parts,
                result_event_id=result_event_id,
            )
            if attempt > 1:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="tool.result_state_persist_deferred_completed",
                    message="Deferred tool result state write completed",
                    payload={
                        "run_id": ctx.deps.run_id,
                        "task_id": ctx.deps.task_id,
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "result_event_id": result_event_id,
                        "attempt": attempt,
                    },
                )
            return
        except (RuntimeError, sqlite3.Error) as exc:
            if attempt > len(_DEFERRED_TOOL_STATE_PERSIST_RETRY_DELAYS_SECONDS):
                raise
            log_event(
                LOGGER,
                logging.DEBUG,
                event="tool.result_state_persist_deferred_retry",
                message="Deferred tool result state write will retry",
                payload={
                    "run_id": ctx.deps.run_id,
                    "task_id": ctx.deps.task_id,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "result_event_id": result_event_id,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                },
            )


async def _mark_tool_running_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    runtime_meta: dict[str, JsonValue],
) -> None:
    current_state = await load_tool_call_state_async(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
    )
    existing_call_state = (
        dict(current_state.call_state) if current_state is not None else {}
    )
    await merge_tool_call_state_async(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        args_preview=_safe_json(args_summary),
        run_yolo=bool(runtime_meta.get("run_yolo") is True),
        approval_mode=_approval_mode_from_meta(runtime_meta),
        approval_status=_approval_status_from_meta(runtime_meta),
        execution_status=ToolExecutionStatus.RUNNING,
        call_state=existing_call_state,
        started_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def _error_payload(exc: Exception) -> ToolError:
    if isinstance(exc, ToolExecutionError):
        return ToolError(
            type=exc.error_type,
            message=str(exc) or exc.__class__.__name__,
            retryable=exc.retryable,
            details=exc.details,
        )

    err_type = "internal_error"
    retryable = False
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        err_type = "validation_error"
        retryable = False
    elif isinstance(exc, KeyError):
        err_type = "not_found"
        retryable = True
    elif isinstance(exc, PermissionError):
        err_type = "permission_error"
        retryable = True
    elif isinstance(exc, sqlite3.OperationalError) and is_retryable_sqlite_error(exc):
        retryable = True

    return ToolError(
        type=err_type,
        message=message,
        retryable=retryable,
    )


def _normalize_result_payload(
    result: object,
) -> tuple[JsonValue | None, JsonValue | None, tuple[ContentPart, ...]]:
    if isinstance(result, ToolResultProjection):
        return (
            _normalize_json_value(result.visible_data),
            _normalize_json_value(result.internal_data),
            tuple(result.tool_content_parts),
        )
    normalized = _normalize_json_value(result)
    return normalized, normalized, ()


async def _invoke_tool_action_with_limits(
    *,
    ctx: ToolContext,
    action: Callable[..., object | Awaitable[object]] | object,
    tool_input: dict[str, JsonValue],
    runtime_meta: dict[str, JsonValue],
    hold_action_capacity: bool = True,
    tool_name: str = "",
) -> object:
    buffer = current_tool_result_commit_buffer()
    if buffer is not None and tool_name in _BATCH_SINGLEFLIGHT_ACTION_TOOLS:
        key = _singleflight_action_key(
            ctx=ctx,
            tool_name=tool_name,
            tool_input=tool_input,
        )

        async def factory() -> object:
            runtime_meta["tool_action_capacity_bypassed_reason"] = (
                "lightweight_batch_tool"
            )
            return await _invoke_tool_action_with_capacity(
                ctx=ctx,
                action=action,
                tool_input=tool_input,
                runtime_meta=runtime_meta,
                hold_action_capacity=False,
            )

        result = await buffer.invoke_action_singleflight_async(
            key=key,
            factory=factory,
        )
        if result.shared:
            runtime_meta["action_queue_wait_ms"] = 0
            runtime_meta["tool_action_capacity_held"] = False
            runtime_meta["tool_action_singleflight_hit"] = True
            runtime_meta["tool_action_singleflight_wait_ms"] = result.wait_ms
            runtime_meta["action_duration_ms"] = 0
        return result.value
    return await _invoke_tool_action_with_capacity(
        ctx=ctx,
        action=action,
        tool_input=tool_input,
        runtime_meta=runtime_meta,
        hold_action_capacity=hold_action_capacity,
    )


async def _invoke_tool_action_with_capacity(
    *,
    ctx: ToolContext,
    action: Callable[..., object | Awaitable[object]] | object,
    tool_input: dict[str, JsonValue],
    runtime_meta: dict[str, JsonValue],
    hold_action_capacity: bool = True,
) -> object:
    _action_capacity.PER_RUN_TOOL_ACTION_CONCURRENCY = PER_RUN_TOOL_ACTION_CONCURRENCY
    _action_capacity.GLOBAL_TOOL_ACTION_SEMAPHORE = _GLOBAL_TOOL_ACTION_SEMAPHORE
    _action_capacity.RUN_TOOL_ACTION_GATES = _RUN_TOOL_ACTION_GATES
    return await invoke_with_tool_action_capacity(
        ctx=ctx,
        runtime_meta=runtime_meta,
        action_factory=partial(
            _invoke_tool_action_async,
            action=action,
            tool_input=tool_input,
        ),
        hold_action_capacity=hold_action_capacity,
    )


def _singleflight_action_key(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_input: dict[str, JsonValue],
) -> str:
    digest = sha256(
        "|".join(
            (
                ctx.deps.workspace_id,
                ctx.deps.task_id,
                tool_name,
                _safe_json(tool_input),
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"{tool_name}:{digest}"


async def _invoke_tool_action_async(
    *,
    action: Callable[..., object | Awaitable[object]] | object,
    tool_input: dict[str, JsonValue],
) -> object:
    if not callable(action):
        return action
    if inspect.iscoroutinefunction(action):
        result = _invoke_tool_action(action=action, tool_input=tool_input)
    else:
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()

        def _invoke_in_context() -> object | Awaitable[object]:
            return context.run(
                _invoke_tool_action,
                action=action,
                tool_input=tool_input,
            )

        result = await loop.run_in_executor(_TOOL_ACTION_EXECUTOR, _invoke_in_context)
    if inspect.isawaitable(result):
        return await result
    return result


def _invoke_tool_action(
    *,
    action: Callable[..., object | Awaitable[object]] | object,
    tool_input: dict[str, JsonValue],
) -> object | Awaitable[object]:
    if not callable(action):
        return action
    signature = inspect.signature(action)
    parameters = list(signature.parameters.values())
    if not parameters:
        no_arg_action = cast(Callable[[], object | Awaitable[object]], action)
        return no_arg_action()
    if _uses_tool_input_dict(parameters):
        input_action = cast(
            Callable[[dict[str, JsonValue]], object | Awaitable[object]],
            action,
        )
        return input_action(tool_input)
    kwargs = _bind_tool_action_kwargs(
        parameters=parameters,
        tool_input=tool_input,
        resolved_annotations=_resolve_tool_action_annotations(action),
    )
    named_action = cast(Callable[..., object | Awaitable[object]], action)
    return named_action(**kwargs)


async def _apply_role_contract_check_async(
    *,
    ctx: ToolContext,
    tool_name: str,
) -> ToolError | None:
    denied_tools = await _role_contract_denied_tools_async(ctx)
    if not denied_tools or tool_name not in denied_tools:
        return None
    log_event(
        LOGGER,
        logging.WARNING,
        event="tool.role_contract.denied",
        message="Tool call denied by role contract",
        payload={
            "role_id": ctx.deps.role_id,
            "tool_name": tool_name,
        },
    )
    return ToolError(
        type="tool_policy_denied",
        message=(
            f"Tool '{tool_name}' is denied for role "
            f"'{ctx.deps.role_id}' by role contract invariant"
        ),
        retryable=False,
    )


async def _role_contract_denied_tools_async(ctx: ToolContext) -> tuple[str, ...]:
    buffer = current_tool_result_commit_buffer()
    if buffer is None:
        return _resolve_role_contract_denied_tools(ctx)
    return await buffer.role_contract_denied_tools_async(
        key=_role_contract_cache_key(ctx),
        factory=partial(_resolve_role_contract_denied_tools, ctx),
    )


def _resolve_role_contract_denied_tools(ctx: ToolContext) -> tuple[str, ...]:
    role: RoleDefinition | None = None
    resolver = getattr(ctx.deps, "runtime_role_resolver", None)
    if resolver is not None:
        try:
            role = resolver.get_effective_role(run_id=None, role_id=ctx.deps.role_id)
        except (KeyError, ValueError, RuntimeError):
            role = None
    if role is None:
        role_registry = getattr(ctx.deps, "role_registry", None)
        if role_registry is None:
            return ()
        try:
            role = role_registry.get(ctx.deps.role_id)
        except (KeyError, ValueError):
            return ()
    if role is None:
        return ()
    return tuple(sorted(runtime_denied_tools_for_role(role)))


def _role_contract_cache_key(ctx: ToolContext) -> str:
    return "|".join(
        (
            ctx.deps.workspace_id,
            ctx.deps.session_id,
            ctx.deps.role_id,
        )
    )


def _tool_middleware_bypass_cache_key(ctx: ToolContext) -> str:
    return "|".join(
        (
            ctx.deps.workspace_id,
            ctx.deps.session_id,
            ctx.deps.role_id,
            str(id(getattr(ctx.deps, "hook_service", None))),
            str(id(ctx.deps.tool_approval_policy)),
        )
    )


def _raise_if_stopped(ctx: ToolContext) -> None:
    ctx.deps.run_control_manager.raise_if_cancelled(
        run_id=ctx.deps.run_id,
        instance_id=ctx.deps.instance_id,
    )


async def _apply_pre_tool_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
) -> tuple[dict[str, JsonValue], ToolError | None, bool]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return tool_input, None, False
    bundle = await hook_service.execute(
        event_input=PreToolUseInput(
            event_name=HookEventName.PRE_TOOL_USE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    if bundle.decision == HookDecisionType.DENY:
        await _apply_permission_denied_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
            denial_source="pre_tool_hook",
            denial_reason=bundle.reason,
            approval_status="hook_denied",
        )
        return (
            tool_input,
            ToolError(
                type="hook_denied",
                message=bundle.reason or "Tool call denied by runtime hooks.",
                retryable=False,
            ),
            False,
        )
    next_args = tool_input
    if isinstance(bundle.updated_input, dict):
        next_args = _normalize_json_object(bundle.updated_input)
    return next_args, None, bundle.decision == HookDecisionType.ASK


async def _apply_pre_execution_guardrails(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    meta: dict[str, JsonValue],
) -> ToolError | None:
    policy = ctx.deps.tool_approval_policy
    guardrail_policy = _guardrail_policy_from_runtime_policy(policy)
    if not guardrail_policy.enabled:
        return None
    context = _runtime_guardrail_context(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    call_count = await _record_guardrail_tool_call_count(
        ctx=ctx,
        context=context,
    )
    allowed_tools: tuple[str, ...] | None = None
    denied_tools: tuple[str, ...] = ()
    if isinstance(policy, ToolApprovalPolicy):
        allowed_tools = await _allowed_tools_for_runtime_policy(ctx=ctx)
        denied_tools = tuple(sorted(policy.denied_tools))
    evaluation = evaluate_pre_execution_guardrails(
        policy=guardrail_policy,
        context=context,
        tool_input=tool_input,
        allowed_tools=allowed_tools,
        denied_tools=denied_tools,
        call_count=call_count,
    )
    if not evaluation.findings:
        return None
    _apply_guardrail_findings_to_meta(meta=meta, evaluation=evaluation)
    await _record_and_publish_guardrail_findings_async(
        ctx=ctx,
        context=context,
        findings=evaluation.findings,
    )
    if not evaluation.blocked:
        return None
    denial = _first_guardrail_block(evaluation.findings)
    meta["approval_required"] = False
    meta["approval_mode"] = ToolApprovalMode.POLICY_EXEMPT.value
    meta["approval_status"] = (
        "denied_by_policy"
        if _guardrail_block_is_policy_boundary(denial)
        else "denied_by_guardrail"
    )
    meta["runtime_policy_decision"] = ToolRuntimeDecision.DENY.value
    meta["runtime_policy_reason"] = denial.message
    return ToolError(
        type=_guardrail_denial_error_type(denial),
        message=denial.message,
        retryable=False,
        details=denial.details,
    )


async def _apply_in_execution_guardrails(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    envelope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    policy = ctx.deps.tool_approval_policy
    guardrail_policy = _guardrail_policy_from_runtime_policy(policy)
    if not guardrail_policy.enabled:
        return envelope
    context = _runtime_guardrail_context(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
    )
    evaluation = evaluate_in_execution_guardrails(
        policy=guardrail_policy,
        context=context,
        tool_input=tool_input,
        result_envelope=envelope,
    )
    if not evaluation.findings:
        return envelope
    meta = _envelope_meta(envelope)
    _apply_guardrail_findings_to_meta(meta=meta, evaluation=evaluation)
    envelope["meta"] = meta
    await _record_and_publish_guardrail_findings_async(
        ctx=ctx,
        context=context,
        findings=evaluation.findings,
    )
    if not evaluation.blocked:
        return envelope
    denial = _first_guardrail_block(evaluation.findings)
    return _visible_envelope(
        ok=False,
        error=ToolError(
            type=_guardrail_denial_error_type(denial),
            message=denial.message,
            retryable=False,
            details=denial.details,
        ),
        meta=meta,
    )


async def _record_guardrail_tool_call_count(
    *,
    ctx: ToolContext,
    context: RuntimeGuardrailContext,
) -> int:
    try:
        return await record_runtime_guardrail_tool_call_async(
            shared_store=ctx.deps.shared_store,
            context=context,
        )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="runtime_guardrail.state_update_failed",
            message="Runtime guardrail could not record tool call count",
            payload={
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "tool_name": context.tool_name,
                "tool_call_id": context.tool_call_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return 1


async def _record_and_publish_guardrail_findings_async(
    *,
    ctx: ToolContext,
    context: RuntimeGuardrailContext,
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> None:
    try:
        _ = await record_runtime_guardrail_findings_async(
            shared_store=ctx.deps.shared_store,
            context=context,
            findings=findings,
        )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="runtime_guardrail.finding_persist_failed",
            message="Runtime guardrail finding could not be persisted",
            payload={
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "tool_name": context.tool_name,
                "tool_call_id": context.tool_call_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
    try:
        await publish_run_event_async(
            ctx.deps.run_event_hub,
            RunEvent(
                session_id=ctx.deps.session_id,
                run_id=ctx.deps.run_id,
                trace_id=ctx.deps.trace_id,
                task_id=ctx.deps.task_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                event_type=RunEventType.RUNTIME_GUARDRAIL_ALERT,
                payload_json=dumps(
                    {
                        "tool_name": context.tool_name,
                        "tool_call_id": context.tool_call_id,
                        "findings": guardrail_findings_payload(findings),
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="runtime_guardrail.alert_publish_failed",
            message="Runtime guardrail alert event could not be published",
            payload={
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "tool_name": context.tool_name,
                "tool_call_id": context.tool_call_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def _runtime_guardrail_context(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
) -> RuntimeGuardrailContext:
    return RuntimeGuardrailContext(
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        session_mode=ctx.deps.session_mode,
        run_kind=ctx.deps.run_kind,
    )


def _guardrail_policy_from_runtime_policy(
    policy: object,
) -> RuntimeGuardrailPolicy:
    if isinstance(policy, ToolApprovalPolicy):
        return policy.guardrails
    return RuntimeGuardrailPolicy(enabled=False)


def _apply_guardrail_findings_to_meta(
    *,
    meta: dict[str, JsonValue],
    evaluation: RuntimeGuardrailEvaluation,
) -> None:
    findings = tuple(
        finding
        for finding in evaluation.findings
        if finding.action != RuntimeGuardrailAction.ALLOW
    )
    if not findings:
        return
    new_blocked = evaluation.blocked_count
    new_warnings = evaluation.warning_count
    new_status = guardrail_meta_status(findings).value
    new_payload = guardrail_findings_payload(findings)
    existing_blocked = meta.get("runtime_guardrail_blocked_count")
    existing_warnings = meta.get("runtime_guardrail_warning_count")
    existing_findings = meta.get("runtime_guardrail_findings")
    existing_status = meta.get("runtime_guardrail_status")
    if isinstance(existing_blocked, int):
        meta["runtime_guardrail_blocked_count"] = existing_blocked + new_blocked
    else:
        meta["runtime_guardrail_blocked_count"] = new_blocked
    if isinstance(existing_warnings, int):
        meta["runtime_guardrail_warning_count"] = existing_warnings + new_warnings
    else:
        meta["runtime_guardrail_warning_count"] = new_warnings
    if isinstance(existing_findings, list):
        existing_list: list[JsonValue] = existing_findings
        meta["runtime_guardrail_findings"] = existing_list + new_payload
    else:
        meta["runtime_guardrail_findings"] = new_payload
    if isinstance(existing_status, str) and existing_status != new_status:
        _status_severity: dict[str, int] = {
            RuntimeGuardrailStatus.BLOCKED.value: 3,
            RuntimeGuardrailStatus.WARNING.value: 2,
            RuntimeGuardrailStatus.PASSED.value: 1,
        }
        existing_rank = _status_severity.get(existing_status, 0)
        new_rank = _status_severity.get(new_status, 0)
        meta["runtime_guardrail_status"] = (
            new_status if new_rank > existing_rank else existing_status
        )
    else:
        meta["runtime_guardrail_status"] = new_status


def _first_guardrail_block(
    findings: tuple[RuntimeGuardrailFinding, ...],
) -> RuntimeGuardrailFinding:
    for finding in findings:
        if finding.action == RuntimeGuardrailAction.DENY:
            return finding
    raise RuntimeError("Expected a runtime guardrail denial finding")


def _guardrail_block_is_policy_boundary(finding: RuntimeGuardrailFinding) -> bool:
    return finding.rule_type in {
        RuntimeGuardrailRuleType.TOOL_ALLOWLIST,
        RuntimeGuardrailRuleType.TOOL_DENYLIST,
    }


def _guardrail_denial_error_type(finding: RuntimeGuardrailFinding) -> str:
    if _guardrail_block_is_policy_boundary(finding):
        return "tool_policy_denied"
    return "runtime_guardrail_denied"


def _envelope_meta(envelope: dict[str, JsonValue]) -> dict[str, JsonValue]:
    raw_meta = envelope.get("meta")
    if not isinstance(raw_meta, dict):
        return {}
    return _normalize_json_object(raw_meta)


async def _apply_permission_request_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
) -> tuple[bool, ToolError | None]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return False, None
    bundle = await hook_service.execute(
        event_input=PermissionRequestInput(
            event_name=HookEventName.PERMISSION_REQUEST,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            approval_required=True,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    if bundle.decision == HookDecisionType.DENY:
        await _apply_permission_denied_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            denial_source="permission_request_hook",
            denial_reason=bundle.reason,
            approval_status="hook_denied",
        )
        return (
            False,
            ToolError(
                type="hook_denied",
                message=bundle.reason or "Tool approval denied by runtime hooks.",
                retryable=False,
            ),
        )
    hook_approved = (
        bool(bundle.executions) and bundle.decision == HookDecisionType.ALLOW
    )
    return hook_approved, None


async def _apply_permission_denied_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    denial_source: str,
    denial_reason: str,
    approval_status: str,
) -> None:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return
    try:
        bundle = await hook_service.execute(
            event_input=PermissionDeniedInput(
                event_name=HookEventName.PERMISSION_DENIED,
                session_id=ctx.deps.session_id,
                run_id=ctx.deps.run_id,
                trace_id=ctx.deps.trace_id,
                task_id=ctx.deps.task_id,
                instance_id=ctx.deps.instance_id,
                role_id=ctx.deps.role_id,
                session_mode=ctx.deps.session_mode,
                run_kind=ctx.deps.run_kind,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
                denial_source=denial_source,
                denial_reason=denial_reason,
                approval_status=approval_status,
            ),
            run_event_hub=ctx.deps.run_event_hub,
        )
        if bundle.additional_context:
            await _enqueue_system_followup_async(
                ctx=ctx,
                content="\n\n".join(
                    str(context).strip()
                    for context in bundle.additional_context
                    if str(context).strip()
                ),
            )
        if bundle.deferred_action:
            await _enqueue_deferred_followup_async(
                ctx=ctx,
                hook_event=HookEventName.PERMISSION_DENIED,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                deferred_action=bundle.deferred_action,
            )
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tools.permission_denied_hook.failed",
            message="PermissionDenied hook failed after tool approval denial",
            payload={
                "run_id": ctx.deps.run_id,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "denial_source": denial_source,
                "approval_status": approval_status,
                "error": str(exc),
            },
        )


async def _apply_post_tool_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
    envelope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return envelope
    bundle = await hook_service.execute(
        event_input=PostToolUseInput(
            event_name=HookEventName.POST_TOOL_USE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            tool_result=envelope,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    return await _apply_post_hook_bundle_to_envelope_async(
        ctx=ctx,
        hook_event=HookEventName.POST_TOOL_USE,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        envelope=envelope,
        bundle=bundle,
    )


async def _apply_post_tool_failure_hooks(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    args_summary: dict[str, JsonValue],
    envelope: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    hook_service = getattr(ctx.deps, "hook_service", None)
    if hook_service is None:
        return envelope
    error_payload = envelope.get("error")
    tool_error = (
        _normalize_json_object(error_payload) if isinstance(error_payload, dict) else {}
    )
    bundle = await hook_service.execute(
        event_input=PostToolUseFailureInput(
            event_name=HookEventName.POST_TOOL_USE_FAILURE,
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=args_summary,
            tool_error=tool_error,
        ),
        run_event_hub=ctx.deps.run_event_hub,
    )
    return await _apply_post_hook_bundle_to_envelope_async(
        ctx=ctx,
        hook_event=HookEventName.POST_TOOL_USE_FAILURE,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        envelope=envelope,
        bundle=bundle,
    )


async def _apply_post_hook_bundle_to_envelope_async(
    *,
    ctx: ToolContext,
    hook_event: HookEventName,
    tool_name: str,
    tool_call_id: str,
    envelope: dict[str, JsonValue],
    bundle: HookDecisionBundle,
) -> dict[str, JsonValue]:
    meta = envelope.get("meta")
    runtime_meta = cast(dict[str, JsonValue], meta) if isinstance(meta, dict) else {}
    if bundle.additional_context:
        runtime_meta["hook_additional_context"] = list(bundle.additional_context)
        await _enqueue_system_followup_async(
            ctx=ctx,
            content="\n\n".join(
                str(context).strip()
                for context in bundle.additional_context
                if str(context).strip()
            ),
        )
    if bundle.deferred_action:
        runtime_meta["hook_deferred_action"] = bundle.deferred_action
        await _enqueue_deferred_followup_async(
            ctx=ctx,
            hook_event=hook_event,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            deferred_action=bundle.deferred_action,
        )
    envelope["meta"] = runtime_meta
    return envelope


async def _enqueue_system_followup_async(
    *,
    ctx: ToolContext,
    content: str,
) -> bool:
    if not content:
        return False
    result = await _system_injection_sink(ctx).enqueue_only_async(
        session_id=ctx.deps.session_id,
        run_id=ctx.deps.run_id,
        trace_id=ctx.deps.trace_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        content=content,
        source=InjectionSource.SYSTEM,
    )
    return result.enqueued


async def _enqueue_deferred_followup_async(
    *,
    ctx: ToolContext,
    hook_event: HookEventName,
    tool_name: str,
    tool_call_id: str,
    deferred_action: str,
) -> None:
    if not await _enqueue_system_followup_async(ctx=ctx, content=deferred_action):
        return
    await publish_run_event_async(
        ctx.deps.run_event_hub,
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=RunEventType.HOOK_DEFERRED,
            payload_json=dumps(
                {
                    "hook_event": hook_event.value,
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "deferred_action": deferred_action,
                },
                ensure_ascii=False,
            ),
        ),
    )


def _system_injection_sink(ctx: ToolContext) -> SystemInjectionSink:
    return SystemInjectionSink(
        injection_manager=ctx.deps.injection_manager,
        run_event_hub=ctx.deps.run_event_hub,
        message_repo=ctx.deps.message_repo,
    )


# noinspection PyTypeHints
async def _observe_tool_result_reminders_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    envelope: dict[str, JsonValue],
) -> None:
    reminder_service = getattr(ctx.deps, "reminder_service", None)
    if reminder_service is None:
        return
    error_payload = envelope.get("error")
    error = (
        cast(dict[str, JsonValue], error_payload)
        if isinstance(error_payload, dict)
        else {}
    )
    meta_payload = envelope.get("meta")
    meta = (
        cast(dict[str, JsonValue], meta_payload)
        if isinstance(meta_payload, dict)
        else {}
    )
    reported_failure = _reported_failure_from_success_envelope(
        tool_name=tool_name,
        envelope=envelope,
    )
    observed_ok = bool(envelope.get("ok") is True)
    error_type = str(error.get("type") or "")
    error_message = str(error.get("message") or "")
    if reported_failure is not None:
        observed_ok = False
        if not error_type:
            error_type = reported_failure[0]
        if not error_message:
            error_message = reported_failure[1]
    observation = ToolResultObservation(
        session_id=ctx.deps.session_id,
        run_id=ctx.deps.run_id,
        trace_id=ctx.deps.trace_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        ok=observed_ok,
        error_type=error_type,
        error_message=error_message,
        retryable=bool(error.get("retryable") is True),
        meta=meta,
    )
    if isinstance(reminder_service, _AsyncToolResultReminderService):
        _ = await reminder_service.observe_tool_result_async(observation)
        return
    _ = await _run_tool_state_work(reminder_service.observe_tool_result, observation)


def _reported_failure_from_success_envelope(
    *,
    tool_name: str,
    envelope: dict[str, JsonValue],
) -> tuple[str, str] | None:
    if envelope.get("ok") is not True:
        return None
    if tool_name != "shell":
        return None
    data_payload = envelope.get("data")
    if not isinstance(data_payload, dict):
        return None
    data = cast(dict[str, JsonValue], data_payload)
    if data.get("status") != "failed":
        return None
    exit_code = data.get("exit_code")
    if not isinstance(exit_code, int) or exit_code == 0:
        return None

    message = _reported_failure_message(data)
    return "reported_failed_status", message


def _reported_failure_message(data: dict[str, JsonValue]) -> str:
    output_excerpt = data.get("output_excerpt")
    if isinstance(output_excerpt, str) and output_excerpt.strip():
        return output_excerpt.strip()

    recent_output = data.get("recent_output")
    if isinstance(recent_output, list):
        lines = [line for line in recent_output if isinstance(line, str)]
        if lines:
            return "\n".join(lines).strip()

    command = data.get("command")
    exit_code = data.get("exit_code")
    if isinstance(command, str) and command.strip() and type(exit_code) is int:
        return f"Command failed with exit code {exit_code}: {command}"
    return "The tool result reported failed status."


async def _handle_tool_approval(
    *,
    ctx: ToolContext,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    approval_args_summary: dict[str, JsonValue] | None,
    meta: dict[str, JsonValue],
    tool_call_id: str,
    approval_request: ToolApprovalRequest | None = None,
    force_approval: bool = False,
) -> tuple[str | None, ToolError | None]:
    decision = await _evaluate_tool_approval_policy(
        ctx=ctx,
        policy=ctx.deps.tool_approval_policy,
        tool_name=tool_name,
        approval_request=approval_request,
    )
    meta["runtime_policy_decision"] = decision.runtime_decision.value
    if decision.reason:
        meta["runtime_policy_reason"] = decision.reason
    if decision.runtime_decision == ToolRuntimeDecision.DENY:
        meta["approval_required"] = False
        meta["approval_mode"] = ToolApprovalMode.POLICY_EXEMPT.value
        meta["approval_status"] = "denied_by_policy"
        return None, ToolError(
            type="tool_policy_denied",
            message=decision.reason or "Tool call denied by runtime policy.",
            retryable=False,
        )
    approval_required = force_approval or decision.required
    run_yolo = _policy_uses_yolo(ctx.deps.tool_approval_policy)
    args_preview = _safe_json(args_summary)
    approval_preview = _safe_json(
        approval_args_summary if approval_args_summary is not None else args_summary
    )
    meta["run_yolo"] = run_yolo
    meta["approval_required"] = approval_required
    meta["approval_mode"] = (
        ToolApprovalMode.YOLO.value
        if run_yolo and not approval_required
        else (
            ToolApprovalMode.POLICY_EXEMPT.value
            if not approval_required
            else ToolApprovalMode.APPROVAL_FLOW.value
        )
    )
    if decision.permission_scope is not None:
        meta["permission_scope"] = decision.permission_scope.value
    if decision.risk_level is not None:
        meta["risk_level"] = decision.risk_level.value
    if decision.target_summary:
        meta["target_summary"] = decision.target_summary
    if decision.source:
        meta["source"] = decision.source
    if decision.execution_surface is not None:
        meta["execution_surface"] = decision.execution_surface.value
    cache_key = approval_request.cache_key if approval_request is not None else ""
    if not approval_required:
        meta["approval_status"] = "not_required"
        return None, None

    hook_allowed, hook_error = await _apply_permission_request_hooks(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        args_summary=args_summary,
    )
    if hook_error is not None:
        return None, hook_error
    if hook_allowed:
        meta["approval_required"] = False
        meta["approval_mode"] = ToolApprovalMode.POLICY_EXEMPT.value
        meta["approval_status"] = "not_required"
        return None, None

    reusable_ticket = await ctx.deps.approval_ticket_repo.find_reusable_async(
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
        cache_key=cache_key,
        signature_args_preview=approval_preview,
    )
    if reusable_ticket is not None:
        if reusable_ticket.status == ApprovalTicketStatus.APPROVED:
            meta["approval_status"] = "approve"
            if reusable_ticket.feedback:
                meta["approval_feedback"] = reusable_ticket.feedback
            return reusable_ticket.tool_call_id, None
        if reusable_ticket.status == ApprovalTicketStatus.REQUESTED:
            return await _wait_for_ticket_resolution(
                ctx=ctx,
                ticket_id=reusable_ticket.tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                args_preview=args_preview,
                meta=meta,
                decision=decision,
            )
        if reusable_ticket.status == ApprovalTicketStatus.DENIED:
            meta["approval_status"] = "deny"
            if reusable_ticket.feedback:
                meta["approval_feedback"] = reusable_ticket.feedback
            await _apply_permission_denied_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=reusable_ticket.tool_call_id,
                tool_input=args_summary,
                denial_source="cached_user_approval",
                denial_reason=reusable_ticket.feedback,
                approval_status="deny",
            )
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_denied",
                message="Tool call was denied by user.",
                retryable=True,
            )
        if reusable_ticket.status == ApprovalTicketStatus.TIMED_OUT:
            meta["approval_status"] = "timeout"
            await _apply_permission_denied_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=reusable_ticket.tool_call_id,
                tool_input=args_summary,
                denial_source="cached_user_approval",
                denial_reason="Tool approval timed out.",
                approval_status="timeout",
            )
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_timeout",
                message="Tool approval timed out.",
                retryable=True,
            )
    ticket = await ctx.deps.approval_ticket_repo.upsert_requested_async(
        tool_call_id=tool_call_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
        metadata=approval_request.metadata if approval_request is not None else None,
        cache_key=cache_key,
        signature_args_preview=approval_preview,
    )
    return await _wait_for_ticket_resolution(
        ctx=ctx,
        ticket_id=ticket.tool_call_id,
        tool_name=tool_name,
        args_summary=args_summary,
        args_preview=args_preview,
        meta=meta,
        decision=decision,
        publish_request=True,
    )


async def _wait_for_ticket_resolution(
    *,
    ctx: ToolContext,
    ticket_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    args_preview: str,
    meta: dict[str, JsonValue],
    decision: ToolApprovalDecision,
    publish_request: bool = False,
) -> tuple[str | None, ToolError | None]:
    existing_approval = ctx.deps.tool_approval_manager.get_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=ticket_id,
    )
    if existing_approval is None:
        ctx.deps.tool_approval_manager.open_approval(
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_name=tool_name,
            args_preview=args_preview,
            risk_level=(
                decision.risk_level.value if decision.risk_level is not None else "high"
            ),
        )
        publish_request = True

    await _ensure_run_runtime_async(ctx=ctx)
    await _update_run_runtime_async(
        ctx=ctx,
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
        active_instance_id=ctx.deps.instance_id,
        active_task_id=ctx.deps.task_id,
        active_role_id=ctx.deps.role_id,
        active_subagent_instance_id=None,
        last_error=None,
    )
    if publish_request:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.approval.requested",
            message="Tool approval requested",
            payload={
                "tool_name": tool_name,
                "tool_call_id": ticket_id,
            },
        )
        await _publish_tool_approval_event_async(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_REQUESTED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "args_preview": args_preview,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
                "risk_level": (
                    decision.risk_level.value
                    if decision.risk_level is not None
                    else "high"
                ),
                "permission_scope": (
                    decision.permission_scope.value
                    if decision.permission_scope is not None
                    else ""
                ),
                "target_summary": decision.target_summary,
                "source": decision.source,
                "execution_surface": (
                    decision.execution_surface.value
                    if decision.execution_surface is not None
                    else ""
                ),
            },
        )
        await _publish_tool_approval_notification_async(
            ctx=ctx,
            tool_call_id=ticket_id,
            tool_name=tool_name,
        )

    try:
        action, feedback = await _run_tool_approval_work(
            ctx.deps.tool_approval_manager.wait_for_approval,
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
            timeout=ctx.deps.tool_approval_policy.timeout_seconds,
        )
    except TimeoutError:
        ctx.deps.tool_approval_manager.close_approval(
            run_id=ctx.deps.run_id,
            tool_call_id=ticket_id,
        )
        try:
            resolved_ticket = await ctx.deps.approval_ticket_repo.resolve_async(
                tool_call_id=ticket_id,
                status=ApprovalTicketStatus.TIMED_OUT,
                expected_status=ApprovalTicketStatus.REQUESTED,
            )
        except ApprovalTicketStatusConflictError:
            resolved_ticket = await ctx.deps.approval_ticket_repo.get_async(ticket_id)
            if resolved_ticket is None:
                raise KeyError(f"Unknown approval ticket: {ticket_id}") from None
        resolved_action, resolved_error = _approval_resolution_from_ticket(
            ticket=resolved_ticket,
            meta=meta,
        )
        if resolved_action == "timeout":
            await _update_run_runtime_async(
                ctx=ctx,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
                active_instance_id=ctx.deps.instance_id,
                active_task_id=ctx.deps.task_id,
                active_role_id=ctx.deps.role_id,
                active_subagent_instance_id=None,
                last_error="Tool approval timed out",
            )
        elif resolved_action == "deny":
            await _update_run_runtime_async(
                ctx=ctx,
                status=RunRuntimeStatus.PAUSED,
                phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
                active_instance_id=ctx.deps.instance_id,
                active_task_id=ctx.deps.task_id,
                active_role_id=ctx.deps.role_id,
                active_subagent_instance_id=None,
                last_error="Tool call was denied by user.",
            )
        log_event(
            LOGGER,
            logging.INFO if resolved_action == "approve" else logging.WARNING,
            event="tool.approval.resolved",
            message=(
                "Tool approval resolved"
                if resolved_action != "timeout"
                else "Tool approval timed out"
            ),
            payload={
                "tool_name": tool_name,
                "tool_call_id": ticket_id,
                "action": resolved_action,
            },
        )
        await _publish_tool_approval_event_async(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "action": resolved_action,
                "feedback": resolved_ticket.feedback,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )
        if resolved_action in {"deny", "timeout"}:
            await _apply_permission_denied_hooks(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=ticket_id,
                tool_input=args_summary,
                denial_source="user_approval",
                denial_reason=resolved_ticket.feedback
                or (
                    "Tool approval timed out."
                    if resolved_action == "timeout"
                    else "Tool call was denied by user."
                ),
                approval_status=resolved_action,
            )
        return ticket_id, resolved_error

    ctx.deps.tool_approval_manager.close_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=ticket_id,
    )
    resolved_status = (
        ApprovalTicketStatus.APPROVED
        if _approval_action_is_approved(action)
        else ApprovalTicketStatus.DENIED
    )
    try:
        resolved_ticket = await ctx.deps.approval_ticket_repo.resolve_async(
            tool_call_id=ticket_id,
            status=resolved_status,
            feedback=feedback,
            expected_status=ApprovalTicketStatus.REQUESTED,
        )
    except ApprovalTicketStatusConflictError:
        resolved_ticket = await ctx.deps.approval_ticket_repo.get_async(ticket_id)
        if resolved_ticket is None:
            raise KeyError(f"Unknown approval ticket: {ticket_id}") from None
    resolved_action, resolved_error = _approval_resolution_from_ticket(
        ticket=resolved_ticket,
        meta=meta,
    )
    log_event(
        LOGGER,
        logging.INFO if resolved_action == "approve" else logging.WARNING,
        event="tool.approval.resolved",
        message="Tool approval resolved",
        payload={
            "tool_name": tool_name,
            "tool_call_id": ticket_id,
            "action": resolved_action,
        },
    )
    await _publish_tool_approval_event_async(
        ctx=ctx,
        event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
        payload={
            "tool_call_id": ticket_id,
            "tool_name": tool_name,
            "action": resolved_action,
            "feedback": resolved_ticket.feedback,
            "instance_id": ctx.deps.instance_id,
            "role_id": ctx.deps.role_id,
        },
    )
    if resolved_action == "deny":
        await _apply_permission_denied_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=ticket_id,
            tool_input=args_summary,
            denial_source="user_approval",
            denial_reason=resolved_ticket.feedback or "Tool call was denied by user.",
            approval_status="deny",
        )
        await _update_run_runtime_async(
            ctx=ctx,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool call was denied by user.",
        )
        return ticket_id, resolved_error
    if resolved_action == "timeout":
        await _apply_permission_denied_hooks(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=ticket_id,
            tool_input=args_summary,
            denial_source="user_approval",
            denial_reason="Tool approval timed out.",
            approval_status="timeout",
        )
        await _update_run_runtime_async(
            ctx=ctx,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool approval timed out",
        )
        return ticket_id, resolved_error

    return ticket_id, None


def _approval_action_is_approved(action: str) -> bool:
    return action in {"approve", "approve_once", "approve_exact", "approve_prefix"}


def _approval_resolution_from_ticket(
    *,
    ticket: ApprovalTicketRecord,
    meta: dict[str, JsonValue],
) -> tuple[str, ToolError | None]:
    if ticket.feedback:
        meta["approval_feedback"] = ticket.feedback
    if ticket.status in {
        ApprovalTicketStatus.APPROVED,
        ApprovalTicketStatus.COMPLETED,
    }:
        meta["approval_status"] = "approve"
        return "approve", None
    if ticket.status == ApprovalTicketStatus.DENIED:
        meta["approval_status"] = "deny"
        return "deny", ToolError(
            type="approval_denied",
            message="Tool call was denied by user.",
            retryable=True,
        )
    meta["approval_status"] = "timeout"
    return "timeout", ToolError(
        type="approval_timeout",
        message="Tool approval timed out.",
        retryable=True,
    )


async def _publish_tool_approval_notification_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
) -> None:
    notification_service = ctx.deps.notification_service
    if notification_service is None:
        return

    role_label = ctx.deps.role_id or "An agent"
    body = f"{role_label} requests approval for {tool_name}."
    _ = await notification_service.emit_async(
        notification_type=NotificationType.TOOL_APPROVAL_REQUESTED,
        title="Approval Required",
        body=body,
        dedupe_key=f"tool_approval_requested:{ctx.deps.run_id}:{tool_call_id}",
        context=NotificationContext(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
        ),
    )


async def _publish_tool_approval_event_async(
    *,
    ctx: ToolContext,
    event_type: RunEventType,
    payload: dict[str, JsonValue],
) -> None:
    await publish_run_event_async(
        ctx.deps.run_event_hub,
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        ),
    )


# noinspection PyTypeHints
def _visible_envelope(
    *,
    ok: bool,
    data: JsonValue = None,
    error: ToolError | None = None,
    meta: dict[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    envelope = ToolResultEnvelope(
        ok=ok,
        data=data,
        error=error,
        meta={} if meta is None else dict(meta),
    )
    return cast(dict[str, JsonValue], envelope.model_dump(mode="json"))


async def _evaluate_tool_approval_policy(
    *,
    ctx: ToolContext,
    policy: ToolApprovalPolicy | _RequiresApprovalPolicy,
    tool_name: str,
    approval_request: ToolApprovalRequest | None,
) -> ToolApprovalDecision:
    if isinstance(policy, ToolApprovalPolicy):
        allowed_tools = await _allowed_tools_for_runtime_policy(ctx=ctx)
        return policy.evaluate(
            tool_name,
            approval_request,
            role_id=ctx.deps.role_id,
            task_id=ctx.deps.task_id,
            allowed_tools=allowed_tools,
        )
    required = cast(bool, policy.requires_approval(tool_name))
    return ToolApprovalDecision(
        required=required,
        runtime_decision=(
            ToolRuntimeDecision.REQUIRE_APPROVAL
            if required
            else ToolRuntimeDecision.ALLOW
        ),
        permission_scope=(
            approval_request.permission_scope if approval_request is not None else None
        ),
        risk_level=approval_request.risk_level
        if approval_request is not None
        else None,
        target_summary=(
            approval_request.target_summary if approval_request is not None else ""
        ),
        source=approval_request.source if approval_request is not None else "",
        execution_surface=(
            approval_request.execution_surface if approval_request is not None else None
        ),
    )


async def _allowed_tools_for_runtime_policy(  # pragma: no cover
    *,
    ctx: ToolContext,
) -> tuple[str, ...] | None:
    buffer = current_tool_result_commit_buffer()
    if buffer is not None:
        key = "|".join(
            (
                ctx.deps.run_id,
                ctx.deps.instance_id,
                ctx.deps.role_id,
                ctx.deps.task_id,
            )
        )

        async def factory() -> tuple[str, ...] | None:
            return await _load_allowed_tools_for_runtime_policy(ctx=ctx)

        return await buffer.allowed_tools_for_policy_async(key=key, factory=factory)
    return await _load_allowed_tools_for_runtime_policy(ctx=ctx)


async def _load_allowed_tools_for_runtime_policy(  # pragma: no cover
    *,
    ctx: ToolContext,
) -> tuple[str, ...] | None:
    try:
        runtime_role_resolver = getattr(ctx.deps, "runtime_role_resolver", None)
        if runtime_role_resolver is not None:
            role = await runtime_role_resolver.get_effective_role_async(
                run_id=ctx.deps.run_id,
                role_id=ctx.deps.role_id,
            )
        else:
            role = ctx.deps.role_registry.get(ctx.deps.role_id)
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.policy.role_resolution_failed",
            message="Tool runtime policy could not resolve role capabilities",
            payload={
                "role_id": ctx.deps.role_id,
                "task_id": ctx.deps.task_id,
                "error_type": type(exc).__name__,
            },
        )
        return ()
    tools = set(
        runtime_tools_for_role(
            role_registry=ctx.deps.role_registry,
            role=role,
            consumer="tools.runtime.execution.allowed_tools",
        )
    )
    tools.update(await _runtime_snapshot_tool_names_for_policy(ctx=ctx))
    denied_tools = set(runtime_denied_tools_for_role(role))
    tools.difference_update(denied_tools)
    return tuple(sorted(tools))


async def _runtime_snapshot_tool_names_for_policy(
    *,
    ctx: ToolContext,
) -> tuple[str, ...]:
    agent_repo = getattr(ctx.deps, "agent_repo", None)
    if not isinstance(agent_repo, _RuntimeToolsAgentRepository):
        return ()
    try:
        instance = await agent_repo.get_instance_async(ctx.deps.instance_id)
    except Exception as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.policy.runtime_tools_snapshot_unavailable",
            message="Tool runtime policy could not load runtime tool snapshot",
            payload={
                "role_id": ctx.deps.role_id,
                "task_id": ctx.deps.task_id,
                "instance_id": ctx.deps.instance_id,
                "error_type": type(exc).__name__,
            },
        )
        return ()
    if not instance.runtime_tools_json.strip():
        return ()
    try:
        snapshot = RuntimeToolsSnapshot.model_validate_json(instance.runtime_tools_json)
    except ValidationError as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.policy.runtime_tools_snapshot_invalid",
            message="Tool runtime policy ignored invalid runtime tool snapshot",
            payload={
                "role_id": ctx.deps.role_id,
                "task_id": ctx.deps.task_id,
                "instance_id": ctx.deps.instance_id,
                "error_type": type(exc).__name__,
            },
        )
        return ()
    return _runtime_snapshot_tool_names(snapshot)


def _runtime_snapshot_tool_names(snapshot: RuntimeToolsSnapshot) -> tuple[str, ...]:
    tools = set[str]()
    for entry in _runtime_snapshot_entries(snapshot):
        tools.add(entry.name)
    return tuple(sorted(tools))


def _runtime_snapshot_entries(
    snapshot: RuntimeToolsSnapshot,
) -> tuple[RuntimeToolSnapshotEntry, ...]:
    return snapshot.local_tools + snapshot.skill_tools + snapshot.mcp_tools


class _RequiresApprovalPolicy(Protocol):
    timeout_seconds: float

    @staticmethod
    def requires_approval(tool_name: str) -> bool:
        raise NotImplementedError


# noinspection PyTypeHints
def _internal_record(
    *,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    tool_content_parts: tuple[ContentPart, ...],
) -> dict[str, JsonValue]:
    record = ToolInternalRecord(
        tool=tool_name,
        visible_result=ToolResultEnvelope.model_validate(visible_envelope),
        internal_data=internal_data,
        runtime_meta=runtime_meta,
        tool_content_parts=tool_content_parts,
    )
    return cast(dict[str, JsonValue], record.model_dump(mode="json"))


def _state_result_record(
    *,
    tool_name: str,
    result_record: dict[str, JsonValue],
    runtime_meta: dict[str, JsonValue],
    result_event_id: int,
) -> dict[str, JsonValue]:
    if (
        tool_name != "read"
        or result_event_id <= 0
        or _json_size_bytes(result_record) <= READ_TOOL_STATE_COMPACT_THRESHOLD_BYTES
    ):
        return result_record
    visible_result = result_record.get("visible_result")
    ok = False
    error: JsonValue | None = None
    if isinstance(visible_result, dict):
        ok = visible_result.get("ok") is True
        error = _normalize_json_value(visible_result.get("error"))
    compact_visible_result: dict[str, JsonValue] = {
        "ok": ok,
        "data": {
            "state_compacted": True,
            "result_event_id": result_event_id,
        },
        "error": error,
        "meta": runtime_meta,
    }
    return _internal_record(
        tool_name=tool_name,
        visible_envelope=compact_visible_result,
        internal_data=None,
        runtime_meta=runtime_meta,
        tool_content_parts=(),
    )


def _json_size_bytes(value: dict[str, JsonValue]) -> int:  # pragma: no cover
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return READ_TOOL_STATE_COMPACT_THRESHOLD_BYTES + 1


async def _persist_tool_record_async(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
    tool_content_parts: tuple[ContentPart, ...] = (),
    result_event_id: int = 0,
) -> None:
    approval_status = _approval_status_from_meta(runtime_meta)
    approval_mode = _approval_mode_from_meta(runtime_meta)
    result_record = _internal_record(
        tool_name=tool_name,
        visible_envelope=visible_envelope,
        internal_data=internal_data,
        runtime_meta=runtime_meta,
        tool_content_parts=tool_content_parts,
    )
    current_state = await load_tool_call_state_async(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
    )
    existing_call_state = (
        dict(current_state.call_state) if current_state is not None else {}
    )
    state_result_record = _state_result_record(
        tool_name=tool_name,
        result_record=result_record,
        runtime_meta=runtime_meta,
        result_event_id=result_event_id,
    )
    await merge_tool_call_state_async(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        args_preview=_safe_json(args_summary),
        run_yolo=bool(runtime_meta.get("run_yolo") is True),
        approval_mode=approval_mode,
        approval_status=approval_status,
        approval_feedback=str(runtime_meta.get("approval_feedback") or ""),
        execution_status=execution_status,
        result_envelope=state_result_record,
        call_state=existing_call_state,
        result_event_id=result_event_id,
        finished_at=datetime.now(tz=timezone.utc).isoformat(),
    )


async def _persist_tool_records_batch_async(  # pragma: no cover
    *,
    items: tuple[ToolResultCommitItem, ...],
    result_event_ids: dict[str, int],
) -> None:
    if not items:
        return
    first = items[0]
    shared_store = first.ctx.deps.shared_store
    task_id = first.ctx.deps.task_id
    current_states = await load_tool_call_states_async(
        shared_store=shared_store,
        task_id=task_id,
        tool_call_ids=tuple(item.tool_call_id for item in items),
    )
    now = datetime.now(tz=timezone.utc).isoformat()
    mutations = []
    for item in items:
        result_event_id = result_event_ids.get(item.tool_call_id, 0)
        result_record = _internal_record(
            tool_name=item.tool_name,
            visible_envelope=item.visible_envelope,
            internal_data=item.internal_data,
            runtime_meta=item.runtime_meta,
            tool_content_parts=item.tool_content_parts,
        )
        current = current_states.get(item.tool_call_id)
        if current is None:
            current = PersistedToolCallState(
                tool_call_id=item.tool_call_id,
                tool_name=item.tool_name,
                run_id=item.ctx.deps.run_id,
                session_id=item.ctx.deps.session_id,
                instance_id=item.ctx.deps.instance_id,
                role_id=item.ctx.deps.role_id,
                args_preview=_safe_json(item.args_summary),
                run_yolo=bool(item.runtime_meta.get("run_yolo") is True),
                approval_mode=(
                    _approval_mode_from_meta(item.runtime_meta)
                    or ToolApprovalMode.UNKNOWN
                ),
                updated_at=now,
            )
        existing_call_state = dict(current.call_state)
        state_result_record = _state_result_record(
            tool_name=item.tool_name,
            result_record=result_record,
            runtime_meta=item.runtime_meta,
            result_event_id=result_event_id,
        )
        next_state = current.model_copy(
            update={
                "tool_name": item.tool_name,
                "run_id": item.ctx.deps.run_id,
                "session_id": item.ctx.deps.session_id,
                "instance_id": item.ctx.deps.instance_id,
                "role_id": item.ctx.deps.role_id,
                "args_preview": _safe_json(item.args_summary),
                "run_yolo": bool(item.runtime_meta.get("run_yolo") is True),
                "approval_mode": (
                    _approval_mode_from_meta(item.runtime_meta)
                    or ToolApprovalMode.UNKNOWN
                ),
                "approval_status": (
                    _approval_status_from_meta(item.runtime_meta)
                    or current.approval_status
                ),
                "approval_feedback": str(
                    item.runtime_meta.get("approval_feedback") or ""
                ),
                "execution_status": item.execution_status,
                "result_envelope": state_result_record,
                "call_state": existing_call_state,
                "result_event_id": result_event_id,
                "finished_at": now,
                "updated_at": now,
            }
        )
        mutations.append(tool_call_state_mutation(task_id=task_id, state=next_state))
    await shared_store.manage_states_async(tuple(mutations))


def _record_tool_metrics_batch_deferred(
    items: tuple[ToolResultCommitItem, ...],
) -> None:
    metric_count = sum(1 for item in items if item.tool_name != "read")
    if metric_count <= 0:
        return
    log_event(
        LOGGER,
        logging.DEBUG,
        event="tool.metrics.batch_record_deferred",
        message="Deferred non-critical tool metrics outside batch flush",
        payload={
            "run_id": items[0].ctx.deps.run_id,
            "task_id": items[0].ctx.deps.task_id,
            "metric_count": metric_count,
        },
    )


def _tool_result_batch_id(items: tuple[ToolResultCommitItem, ...]) -> str:
    first = items[0]
    digest = sha256(
        "|".join(
            (
                first.ctx.deps.trace_id,
                first.ctx.deps.task_id,
                first.tool_call_id,
                str(len(items)),
            )
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"toolresultbatch_{digest}"


def _approval_status_from_meta(
    runtime_meta: dict[str, JsonValue],
) -> ToolApprovalStatus | None:
    approval_text = str(runtime_meta.get("approval_status") or "").strip().lower()
    if approval_text in {
        ToolApprovalStatus.APPROVE.value,
        "approve_once",
        "approve_exact",
        "approve_prefix",
    }:
        return ToolApprovalStatus.APPROVE
    if approval_text == ToolApprovalStatus.DENY.value:
        return ToolApprovalStatus.DENY
    if approval_text == ToolApprovalStatus.TIMEOUT.value:
        return ToolApprovalStatus.TIMEOUT
    if approval_text == ToolApprovalStatus.NOT_REQUIRED.value:
        return ToolApprovalStatus.NOT_REQUIRED
    return None


def _approval_mode_from_meta(
    runtime_meta: dict[str, JsonValue],
) -> ToolApprovalMode | None:
    approval_mode = str(runtime_meta.get("approval_mode") or "").strip().lower()
    for candidate in ToolApprovalMode:
        if approval_mode == candidate.value:
            return candidate
    return None


def _policy_uses_yolo(policy: object) -> bool:
    return bool(getattr(policy, "yolo", False))
