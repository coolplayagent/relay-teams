# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from json import dumps
from typing import cast
from uuid import uuid4

from agent_teams.logger import get_logger, log_event, log_tool_call, log_tool_error
from agent_teams.notifications import NotificationContext, NotificationType
from agent_teams.runs.enums import RunEventType
from agent_teams.runs.models import RunEvent
from agent_teams.shared_types.json_types import JsonObject, JsonValue
from agent_teams.tools.runtime.context import ToolContext
from agent_teams.tools.runtime.models import ToolError, ToolResultEnvelope

LOGGER = get_logger(__name__)


async def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: JsonObject,
    action: Callable[[], object | Awaitable[object]] | object,
) -> JsonObject:
    """Run a tool action with approval, logging, and normalized envelopes."""
    started = time.perf_counter()
    if LOGGER.isEnabledFor(logging.DEBUG):
        log_event(
            LOGGER,
            logging.DEBUG,
            event="runtime.debug",
            message=(
                f"[tool:start] tool={tool_name} run={ctx.deps.run_id} "
                f"task={ctx.deps.task_id} instance={ctx.deps.instance_id} "
                f"args={_safe_json(args_summary)}"
            ),
        )
    else:
        log_tool_call(ctx.deps.role_id, tool_name, args_summary)

    meta: JsonObject = {}
    _raise_if_stopped(ctx)
    approval_error = await _handle_tool_approval(
        ctx=ctx,
        tool_name=tool_name,
        args_summary=args_summary,
        meta=meta,
    )
    if approval_error is not None:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        meta["duration_ms"] = elapsed_ms
        return _envelope(
            ok=False,
            tool_name=tool_name,
            error=approval_error,
            meta=meta,
        )

    try:
        _raise_if_stopped(ctx)
        result = action() if callable(action) else action
        if inspect.isawaitable(result):
            result = await result
        _raise_if_stopped(ctx)
        normalized_result = _normalize_json_value(result)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        meta["duration_ms"] = elapsed_ms

        if LOGGER.isEnabledFor(logging.DEBUG):
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message=f"[tool:ok] tool={tool_name} elapsed_ms={elapsed_ms}",
            )

        return _envelope(
            ok=True,
            tool_name=tool_name,
            data=normalized_result,
            meta=meta,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        meta["duration_ms"] = elapsed_ms
        error = _error_payload(exc)

        if LOGGER.isEnabledFor(logging.DEBUG):
            log_event(
                LOGGER,
                logging.DEBUG,
                event="runtime.debug",
                message=(
                    f"[tool:error] tool={tool_name} elapsed_ms={elapsed_ms} "
                    f"err_type={error.type} msg={error.message}"
                ),
            )
        else:
            compact = json.dumps(
                {
                    "tool": tool_name,
                    "type": error.type,
                    "message": error.message,
                },
                ensure_ascii=False,
            )
            log_tool_error(ctx.deps.role_id, compact)
        return _envelope(
            ok=False,
            tool_name=tool_name,
            error=error,
            meta=meta,
        )


def _error_payload(exc: Exception) -> ToolError:
    err_type = "internal_error"
    retryable = False
    suggested_fix: str | None = "Retry with corrected tool parameters."
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        err_type = "validation_error"
        retryable = True
        if "scope_type" in message:
            suggested_fix = "Use one of allowed_values for scope_type."
    elif isinstance(exc, KeyError):
        err_type = "not_found"
        retryable = True
        suggested_fix = "Check IDs and ensure target exists before retrying."
    elif isinstance(exc, PermissionError):
        err_type = "permission_error"
        retryable = True
        suggested_fix = "Use a path within workspace or permitted scope."

    return ToolError(
        type=err_type,
        message=message,
        retryable=retryable,
        suggested_fix=suggested_fix,
    )


def _safe_json(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) > 500:
        return text[:500] + "...(truncated)"
    return text


def _normalize_json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_normalize_json_value(item) for item in items]
    if isinstance(value, dict):
        entries = cast(dict[object, object], value)
        normalized: JsonObject = {}
        for key, item in entries.items():
            normalized[str(key)] = _normalize_json_value(item)
        return normalized
    return str(value)


def _raise_if_stopped(ctx: ToolContext) -> None:
    ctx.deps.run_control_manager.raise_if_cancelled(
        run_id=ctx.deps.run_id,
        instance_id=ctx.deps.instance_id,
    )


async def _handle_tool_approval(
    *,
    ctx: ToolContext,
    tool_name: str,
    args_summary: JsonObject,
    meta: JsonObject,
) -> ToolError | None:
    approval_required = ctx.deps.tool_approval_policy.requires_approval(tool_name)
    meta["approval_required"] = approval_required
    if not approval_required:
        meta["approval_status"] = "not_required"
        return None

    tool_call_id = ctx.tool_call_id or f"toolcall_{uuid4().hex[:12]}"
    args_preview = _safe_json(args_summary)
    ctx.deps.tool_approval_manager.open_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=tool_call_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
        risk_level="high",
    )
    _publish_tool_approval_event(
        ctx=ctx,
        event_type=RunEventType.TOOL_APPROVAL_REQUESTED,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args_preview": args_preview,
            "instance_id": ctx.deps.instance_id,
            "role_id": ctx.deps.role_id,
            "risk_level": "high",
        },
    )
    _publish_tool_approval_notification(
        ctx=ctx,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    )
    try:
        action, feedback = await asyncio.to_thread(
            ctx.deps.tool_approval_manager.wait_for_approval,
            run_id=ctx.deps.run_id,
            tool_call_id=tool_call_id,
            timeout=ctx.deps.tool_approval_policy.timeout_seconds,
        )
    except TimeoutError:
        ctx.deps.tool_approval_manager.close_approval(
            run_id=ctx.deps.run_id,
            tool_call_id=tool_call_id,
        )
        meta["approval_status"] = "timeout"
        _publish_tool_approval_event(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
            payload={
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "action": "timeout",
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )
        return ToolError(
            type="approval_timeout",
            message="Tool approval timed out.",
            retryable=True,
            suggested_fix="Approve or deny this tool call via tool-approvals API and retry.",
        )

    ctx.deps.tool_approval_manager.close_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=tool_call_id,
    )
    meta["approval_status"] = action
    if feedback:
        meta["approval_feedback"] = feedback
    _publish_tool_approval_event(
        ctx=ctx,
        event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "action": action,
            "feedback": feedback,
            "instance_id": ctx.deps.instance_id,
            "role_id": ctx.deps.role_id,
        },
    )
    if action == "deny":
        return ToolError(
            type="approval_denied",
            message="Tool call was denied by user.",
            retryable=True,
            suggested_fix="Adjust the approach and request a safer tool call.",
        )
    return None


def _publish_tool_approval_notification(
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
    _ = notification_service.emit(
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


def _publish_tool_approval_event(
    *,
    ctx: ToolContext,
    event_type: RunEventType,
    payload: JsonObject,
) -> None:
    ctx.deps.run_event_hub.publish(
        RunEvent(
            session_id=ctx.deps.session_id,
            run_id=ctx.deps.run_id,
            trace_id=ctx.deps.trace_id,
            task_id=ctx.deps.task_id,
            instance_id=ctx.deps.instance_id,
            role_id=ctx.deps.role_id,
            event_type=event_type,
            payload_json=dumps(payload, ensure_ascii=False),
        )
    )


def _envelope(
    *,
    ok: bool,
    tool_name: str,
    data: JsonValue = None,
    error: ToolError | None = None,
    meta: JsonObject | None = None,
) -> JsonObject:
    envelope = ToolResultEnvelope(
        ok=ok,
        tool=tool_name,
        data=data,
        error=error,
        meta=meta or {},
    )
    return cast(JsonObject, envelope.model_dump(mode="json"))
