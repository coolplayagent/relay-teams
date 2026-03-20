# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import asyncio
import inspect
import json
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from json import dumps
from typing import cast
from uuid import uuid4

from agent_teams.logger import get_logger, log_event, log_tool_error
from agent_teams.notifications import NotificationContext, NotificationType
from agent_teams.persistence import is_retryable_sqlite_error
from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_models import RunEvent

from agent_teams.tools.runtime.approval_ticket_repo import ApprovalTicketStatus
from agent_teams.sessions.runs.run_runtime_repo import RunRuntimePhase, RunRuntimeStatus
from agent_teams.trace import trace_span
from agent_teams.tools.runtime.context import ToolContext
from agent_teams.tools.runtime.models import (
    ToolError,
    ToolInternalRecord,
    ToolResultEnvelope,
    ToolResultProjection,
)
from agent_teams.tools.runtime.persisted_state import (
    ToolApprovalStatus,
    ToolExecutionStatus,
    merge_tool_call_state,
)

LOGGER = get_logger(__name__)


async def execute_tool(
    ctx: ToolContext,
    *,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    action: Callable[[], object | Awaitable[object]] | object,
) -> dict[str, JsonValue]:
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
        _raise_if_stopped(ctx)
        approval_ticket_id, approval_error = await _handle_tool_approval(
            ctx=ctx,
            tool_name=tool_name,
            args_summary=args_summary,
            meta=meta,
            tool_call_id=tool_call_id,
        )
        if approval_error is not None:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms
            envelope = _visible_envelope(
                ok=False,
                error=approval_error,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            return envelope

        ctx.deps.run_runtime_repo.ensure(
            run_id=ctx.deps.run_id,
            session_id=ctx.deps.session_id,
            root_task_id=ctx.deps.task_id,
        )
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.COORDINATOR_RUNNING
            if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)
            else RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=(
                None
                if ctx.deps.role_registry.is_coordinator_role(ctx.deps.role_id)
                else ctx.deps.instance_id
            ),
            last_error=None,
        )

        try:
            _raise_if_stopped(ctx)
            result = action() if callable(action) else action
            if inspect.isawaitable(result):
                result = await result
            _raise_if_stopped(ctx)
            visible_data, internal_data = _normalize_result_payload(result)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms

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
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=internal_data,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.COMPLETED,
            )
            if approval_ticket_id:
                ctx.deps.approval_ticket_repo.mark_completed(approval_ticket_id)
            return envelope
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            meta["duration_ms"] = elapsed_ms
            error = _error_payload(exc)

            compact = json.dumps(
                {
                    "tool": tool_name,
                    "type": error.type,
                    "message": error.message,
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
                },
            )
            envelope = _visible_envelope(
                ok=False,
                error=error,
            )
            _persist_tool_record(
                ctx=ctx,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args_summary=args_summary,
                visible_envelope=envelope,
                internal_data=None,
                runtime_meta=meta,
                execution_status=ToolExecutionStatus.FAILED,
            )
            if approval_ticket_id:
                ctx.deps.approval_ticket_repo.mark_completed(approval_ticket_id)
            return envelope


def _error_payload(exc: Exception) -> ToolError:
    err_type = "internal_error"
    retryable = False
    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, ValueError):
        err_type = "validation_error"
        retryable = True
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
) -> tuple[JsonValue | None, JsonValue | None]:
    if isinstance(result, ToolResultProjection):
        return (
            _normalize_json_value(result.visible_data),
            _normalize_json_value(result.internal_data),
        )
    normalized = _normalize_json_value(result)
    return normalized, normalized


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
        normalized: dict[str, JsonValue] = {}
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
    args_summary: dict[str, JsonValue],
    meta: dict[str, JsonValue],
    tool_call_id: str,
) -> tuple[str | None, ToolError | None]:
    approval_required = ctx.deps.tool_approval_policy.requires_approval(tool_name)
    args_preview = _safe_json(args_summary)
    meta["approval_required"] = approval_required
    if not approval_required:
        meta["approval_status"] = "not_required"
        return None, None

    reusable_ticket = ctx.deps.approval_ticket_repo.find_reusable(
        run_id=ctx.deps.run_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
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
                args_preview=args_preview,
                meta=meta,
            )
        if reusable_ticket.status == ApprovalTicketStatus.DENIED:
            meta["approval_status"] = "deny"
            if reusable_ticket.feedback:
                meta["approval_feedback"] = reusable_ticket.feedback
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_denied",
                message="Tool call was denied by user.",
                retryable=True,
            )
        if reusable_ticket.status == ApprovalTicketStatus.TIMED_OUT:
            meta["approval_status"] = "timeout"
            return reusable_ticket.tool_call_id, ToolError(
                type="approval_timeout",
                message="Tool approval timed out.",
                retryable=True,
            )
    ticket = ctx.deps.approval_ticket_repo.upsert_requested(
        tool_call_id=tool_call_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_name=tool_name,
        args_preview=args_preview,
    )
    return await _wait_for_ticket_resolution(
        ctx=ctx,
        ticket_id=ticket.tool_call_id,
        tool_name=tool_name,
        args_preview=args_preview,
        meta=meta,
        publish_request=True,
    )


async def _wait_for_ticket_resolution(
    *,
    ctx: ToolContext,
    ticket_id: str,
    tool_name: str,
    args_preview: str,
    meta: dict[str, JsonValue],
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
            risk_level="high",
        )
        publish_request = True

    ctx.deps.run_runtime_repo.ensure(
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        root_task_id=ctx.deps.task_id,
    )
    ctx.deps.run_runtime_repo.update(
        ctx.deps.run_id,
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
        _publish_tool_approval_event(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_REQUESTED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "args_preview": args_preview,
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
                "risk_level": "high",
            },
        )
        _publish_tool_approval_notification(
            ctx=ctx,
            tool_call_id=ticket_id,
            tool_name=tool_name,
        )

    try:
        action, feedback = await asyncio.to_thread(
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
        ctx.deps.approval_ticket_repo.resolve(
            tool_call_id=ticket_id,
            status=ApprovalTicketStatus.TIMED_OUT,
        )
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool approval timed out",
        )
        meta["approval_status"] = "timeout"
        log_event(
            LOGGER,
            logging.WARNING,
            event="tool.approval.resolved",
            message="Tool approval timed out",
            payload={
                "tool_name": tool_name,
                "tool_call_id": ticket_id,
                "action": "timeout",
            },
        )
        _publish_tool_approval_event(
            ctx=ctx,
            event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
            payload={
                "tool_call_id": ticket_id,
                "tool_name": tool_name,
                "action": "timeout",
                "instance_id": ctx.deps.instance_id,
                "role_id": ctx.deps.role_id,
            },
        )
        return ticket_id, ToolError(
            type="approval_timeout",
            message="Tool approval timed out.",
            retryable=True,
        )

    ctx.deps.tool_approval_manager.close_approval(
        run_id=ctx.deps.run_id,
        tool_call_id=ticket_id,
    )
    resolved_status = (
        ApprovalTicketStatus.APPROVED
        if action == "approve"
        else ApprovalTicketStatus.DENIED
    )
    ctx.deps.approval_ticket_repo.resolve(
        tool_call_id=ticket_id,
        status=resolved_status,
        feedback=feedback,
    )
    meta["approval_status"] = action
    if feedback:
        meta["approval_feedback"] = feedback
    log_event(
        LOGGER,
        logging.INFO if action == "approve" else logging.WARNING,
        event="tool.approval.resolved",
        message="Tool approval resolved",
        payload={
            "tool_name": tool_name,
            "tool_call_id": ticket_id,
            "action": action,
        },
    )
    _publish_tool_approval_event(
        ctx=ctx,
        event_type=RunEventType.TOOL_APPROVAL_RESOLVED,
        payload={
            "tool_call_id": ticket_id,
            "tool_name": tool_name,
            "action": action,
            "feedback": feedback,
            "instance_id": ctx.deps.instance_id,
            "role_id": ctx.deps.role_id,
        },
    )
    if action == "deny":
        ctx.deps.run_runtime_repo.update(
            ctx.deps.run_id,
            status=RunRuntimeStatus.PAUSED,
            phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            active_instance_id=ctx.deps.instance_id,
            active_task_id=ctx.deps.task_id,
            active_role_id=ctx.deps.role_id,
            active_subagent_instance_id=None,
            last_error="Tool call was denied by user.",
        )
        return ticket_id, ToolError(
            type="approval_denied",
            message="Tool call was denied by user.",
            retryable=True,
        )

    return ticket_id, None


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
    payload: dict[str, JsonValue],
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


def _visible_envelope(
    *,
    ok: bool,
    data: JsonValue = None,
    error: ToolError | None = None,
) -> dict[str, JsonValue]:
    envelope = ToolResultEnvelope(
        ok=ok,
        data=data,
        error=error,
    )
    return cast(dict[str, JsonValue], envelope.model_dump(mode="json"))


def _internal_record(
    *,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    record = ToolInternalRecord(
        tool=tool_name,
        visible_result=ToolResultEnvelope.model_validate(visible_envelope),
        internal_data=internal_data,
        runtime_meta=runtime_meta,
    )
    return cast(dict[str, JsonValue], record.model_dump(mode="json"))


def _persist_tool_record(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_name: str,
    args_summary: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    runtime_meta: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> None:
    approval_status = _approval_status_from_meta(runtime_meta)
    merge_tool_call_state(
        shared_store=ctx.deps.shared_store,
        task_id=ctx.deps.task_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        args_preview=_safe_json(args_summary),
        approval_status=approval_status,
        approval_feedback=str(runtime_meta.get("approval_feedback") or ""),
        execution_status=execution_status,
        result_envelope=_internal_record(
            tool_name=tool_name,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            runtime_meta=runtime_meta,
        ),
    )


def _approval_status_from_meta(
    runtime_meta: dict[str, JsonValue],
) -> ToolApprovalStatus | None:
    approval_text = str(runtime_meta.get("approval_status") or "").strip().lower()
    if approval_text == ToolApprovalStatus.APPROVE.value:
        return ToolApprovalStatus.APPROVE
    if approval_text == ToolApprovalStatus.DENY.value:
        return ToolApprovalStatus.DENY
    if approval_text == ToolApprovalStatus.TIMEOUT.value:
        return ToolApprovalStatus.TIMEOUT
    if approval_text == ToolApprovalStatus.NOT_REQUIRED.value:
        return ToolApprovalStatus.NOT_REQUIRED
    return None
