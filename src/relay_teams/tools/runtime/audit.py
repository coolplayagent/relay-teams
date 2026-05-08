# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import sqlite3
from hashlib import sha256
from typing import Protocol

from pydantic import JsonValue

from relay_teams.audit import AuditEventCreate, AuditEventType, AuditService
from relay_teams.logger import get_logger, log_event
from relay_teams.paths import path_is_file, read_bytes_file
from relay_teams.tools.runtime.context import ToolContext
from relay_teams.tools.runtime.json_helpers import (
    normalize_json_value as _normalize_json_value,
)
from relay_teams.tools.runtime.persisted_state import ToolExecutionStatus

LOGGER = get_logger(__name__)
_AUDITED_FILE_WRITE_TOOLS = frozenset({"write", "write_tmp", "edit", "notebook_edit"})
_AUDITED_TOOL_NAMES = _AUDITED_FILE_WRITE_TOOLS | frozenset(
    {"shell", "orch_dispatch_task"}
)
_AUDIT_REASON_LIMIT = 4_000
SECURITY_AUDIT_RECORD_TIMEOUT_SECONDS = 2.0


class WorkspaceFileDigest(Protocol):
    def __call__(
        self,
        *,
        ctx: ToolContext,
        logical_path: str,
    ) -> tuple[str | None, int | None, str | None]:
        raise NotImplementedError


async def record_security_audit_event_best_effort_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> None:
    try:
        await asyncio.wait_for(
            _record_security_audit_event_async(
                ctx=ctx,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tool_input=tool_input,
                visible_envelope=visible_envelope,
                internal_data=internal_data,
                execution_status=execution_status,
            ),
            timeout=SECURITY_AUDIT_RECORD_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, RuntimeError, sqlite3.Error) as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="security.audit.record_deferred",
            message="Security audit write skipped on tool hot path",
            payload={
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "error_type": type(exc).__name__,
            },
        )


async def _record_security_audit_event_best_effort_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> None:
    await record_security_audit_event_best_effort_async(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_input=tool_input,
        visible_envelope=visible_envelope,
        internal_data=internal_data,
        execution_status=execution_status,
    )


async def _record_security_audit_event_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> None:
    if tool_name not in _AUDITED_TOOL_NAMES:
        return
    service = _audit_service(ctx)
    if service is None:
        return
    event = await _build_security_audit_event_async(
        ctx=ctx,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_input=tool_input,
        visible_envelope=visible_envelope,
        internal_data=internal_data,
        execution_status=execution_status,
    )
    if event is None:
        return
    try:
        await service.record_event_async(event)
    except (RuntimeError, sqlite3.Error, ValueError) as exc:
        log_event(
            LOGGER,
            logging.WARNING,
            event="security.audit.record_failed",
            message="Security audit event could not be recorded",
            payload={
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "audit_event_type": event.event_type.value,
                "run_id": ctx.deps.run_id,
                "task_id": ctx.deps.task_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def _audit_service(ctx: ToolContext) -> AuditService | None:
    return ctx.deps.audit_service


async def _build_security_audit_event_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> AuditEventCreate | None:
    if tool_name in _AUDITED_FILE_WRITE_TOOLS:
        return await _build_file_write_audit_event_async(
            ctx=ctx,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
            visible_envelope=visible_envelope,
            internal_data=internal_data,
            execution_status=execution_status,
        )
    if tool_name == "shell":
        return _build_shell_command_audit_event(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
            visible_envelope=visible_envelope,
            execution_status=execution_status,
        )
    if tool_name == "orch_dispatch_task":
        return _build_coordinator_decision_audit_event(
            ctx=ctx,
            tool_call_id=tool_call_id,
            tool_input=tool_input,
            visible_envelope=visible_envelope,
            execution_status=execution_status,
        )
    return None


async def _build_file_write_audit_event_async(
    *,
    ctx: ToolContext,
    tool_name: str,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    internal_data: JsonValue | None,
    execution_status: ToolExecutionStatus,
) -> AuditEventCreate | None:
    target = _file_write_target(tool_name, tool_input, internal_data)
    if target is None:
        return None
    content_digest, content_size_bytes, digest_error = await asyncio.to_thread(
        _workspace_file_digest,
        ctx=ctx,
        logical_path=target,
    )
    metadata = _base_audit_metadata(
        tool_name=tool_name,
        visible_envelope=visible_envelope,
        execution_status=execution_status,
    )
    if digest_error is not None:
        metadata["content_digest_error"] = digest_error
    _add_file_write_metadata(
        metadata=metadata,
        tool_name=tool_name,
        tool_input=tool_input,
        internal_data=internal_data,
    )
    return AuditEventCreate(
        event_type=AuditEventType.FILE_WRITE,
        trace_id=ctx.deps.trace_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_call_id=tool_call_id,
        action=_file_write_action(tool_name),
        target=target,
        content_digest=content_digest,
        content_size_bytes=content_size_bytes,
        outcome=_audit_outcome(
            visible_envelope=visible_envelope,
            execution_status=execution_status,
        ),
        metadata=metadata,
    )


def _build_shell_command_audit_event(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> AuditEventCreate | None:
    command = _string_value(tool_input.get("command"))
    if command is None:
        return None
    metadata = _base_audit_metadata(
        tool_name="shell",
        visible_envelope=visible_envelope,
        execution_status=execution_status,
    )
    _copy_json_metadata(metadata, tool_input, "workdir")
    _copy_json_metadata(metadata, tool_input, "background")
    _copy_json_metadata(metadata, tool_input, "tty")
    _copy_json_metadata(metadata, tool_input, "yield_time_ms")
    _copy_json_metadata(metadata, tool_input, "timeout_ms")
    _copy_result_metadata(metadata, visible_envelope, "status")
    _copy_result_metadata(metadata, visible_envelope, "exit_code")
    return AuditEventCreate(
        event_type=AuditEventType.SHELL_COMMAND,
        trace_id=ctx.deps.trace_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_call_id=tool_call_id,
        action="execute_shell_command",
        target=_truncate_text(command, 200)[0],
        command=command,
        outcome=_audit_outcome(
            visible_envelope=visible_envelope,
            execution_status=execution_status,
        ),
        metadata=metadata,
    )


def _build_coordinator_decision_audit_event(
    *,
    ctx: ToolContext,
    tool_call_id: str,
    tool_input: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> AuditEventCreate | None:
    task_id = _string_value(tool_input.get("task_id"))
    selected_role_id = _string_value(tool_input.get("role_id"))
    if task_id is None or selected_role_id is None:
        return None
    prompt = _string_value(tool_input.get("prompt")) or ""
    reason_source = (
        prompt.strip()
        or "Coordinator dispatched task with the default execution prompt."
    )
    decision_reason, reason_truncated = _truncate_text(
        reason_source,
        _AUDIT_REASON_LIMIT,
    )
    metadata = _base_audit_metadata(
        tool_name="orch_dispatch_task",
        visible_envelope=visible_envelope,
        execution_status=execution_status,
    )
    metadata["dispatched_task_id"] = task_id
    metadata["selected_role_id"] = selected_role_id
    metadata["decision_reason_digest"] = _text_digest(reason_source)
    metadata["decision_reason_length"] = len(reason_source)
    metadata["decision_reason_truncated"] = reason_truncated
    return AuditEventCreate(
        event_type=AuditEventType.COORDINATOR_DECISION,
        trace_id=ctx.deps.trace_id,
        run_id=ctx.deps.run_id,
        session_id=ctx.deps.session_id,
        task_id=ctx.deps.task_id,
        instance_id=ctx.deps.instance_id,
        role_id=ctx.deps.role_id,
        tool_call_id=tool_call_id,
        action="dispatch_task",
        target=f"task:{task_id}->role:{selected_role_id}",
        decision_reason=decision_reason,
        outcome=_audit_outcome(
            visible_envelope=visible_envelope,
            execution_status=execution_status,
        ),
        metadata=metadata,
    )


def _base_audit_metadata(
    *,
    tool_name: str,
    visible_envelope: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "tool_name": tool_name,
        "execution_status": execution_status.value,
        "tool_ok": visible_envelope.get("ok") is True,
    }
    error_type = _visible_error_type(visible_envelope)
    if error_type is not None:
        metadata["error_type"] = error_type
    return metadata


def _audit_outcome(
    *,
    visible_envelope: dict[str, JsonValue],
    execution_status: ToolExecutionStatus,
) -> str:
    if visible_envelope.get("ok") is True and execution_status == (
        ToolExecutionStatus.COMPLETED
    ):
        return "completed"
    return "failed"


def _visible_error_type(visible_envelope: dict[str, JsonValue]) -> str | None:
    error = visible_envelope.get("error")
    if not isinstance(error, dict):
        return None
    value = error.get("type")
    return value if isinstance(value, str) and value else None


def _copy_json_metadata(
    metadata: dict[str, JsonValue],
    source: dict[str, JsonValue],
    key: str,
) -> None:
    if key in source:
        metadata[key] = source[key]


def _copy_result_metadata(
    metadata: dict[str, JsonValue],
    visible_envelope: dict[str, JsonValue],
    key: str,
) -> None:
    data = visible_envelope.get("data")
    if not isinstance(data, dict):
        return
    value = data.get(key)
    if value is not None:
        metadata[key] = _normalize_json_value(value)


def _add_file_write_metadata(
    *,
    metadata: dict[str, JsonValue],
    tool_name: str,
    tool_input: dict[str, JsonValue],
    internal_data: JsonValue | None,
) -> None:
    content_field = _file_content_input_field(tool_name)
    if content_field is not None:
        content = _string_value(tool_input.get(content_field))
        if content is not None:
            metadata["input_content_digest"] = _text_digest(content)
            metadata["input_content_length"] = len(content)
    if isinstance(internal_data, dict):
        created = internal_data.get("created")
        if isinstance(created, bool):
            metadata["created"] = created
        diff_summary = internal_data.get("diff_summary")
        if isinstance(diff_summary, str) and diff_summary:
            metadata["diff_summary"] = diff_summary


def _file_content_input_field(tool_name: str) -> str | None:
    if tool_name in {"write", "write_tmp"}:
        return "content"
    if tool_name == "edit":
        return "new_string"
    if tool_name == "notebook_edit":
        return "new_source"
    return None


def _file_write_target(
    tool_name: str,
    tool_input: dict[str, JsonValue],
    internal_data: JsonValue | None,
) -> str | None:
    if tool_name == "write_tmp":
        internal_path = _internal_data_text(internal_data, "path")
        if internal_path is not None:
            return internal_path
        raw_tmp_path = _string_value(tool_input.get("path"))
        if raw_tmp_path is None:
            return None
        if raw_tmp_path == "tmp" or raw_tmp_path.startswith(("tmp/", "tmp\\")):
            return raw_tmp_path
        return f"tmp/{raw_tmp_path}"
    return _string_value(tool_input.get("path")) or _internal_data_text(
        internal_data,
        "path",
    )


def _file_write_action(tool_name: str) -> str:
    if tool_name == "edit":
        return "edit_file"
    if tool_name == "notebook_edit":
        return "edit_notebook"
    if tool_name == "write_tmp":
        return "write_tmp_file"
    return "write_file"


def _workspace_file_digest(
    *,
    ctx: ToolContext,
    logical_path: str,
) -> tuple[str | None, int | None, str | None]:
    try:
        file_path = ctx.deps.workspace.resolve_path(logical_path, write=False)
        if not path_is_file(file_path):
            return None, None, "target is not a file"
        content = read_bytes_file(file_path)
    except (OSError, RuntimeError, ValueError) as exc:
        return None, None, f"{type(exc).__name__}: {exc}"
    return f"sha256:{sha256(content).hexdigest()}", len(content), None


def set_workspace_file_digest_override_for_testing(
    digest: WorkspaceFileDigest,
) -> None:
    global _workspace_file_digest
    _workspace_file_digest = digest


def _internal_data_text(internal_data: JsonValue | None, key: str) -> str | None:
    if not isinstance(internal_data, dict):
        return None
    return _string_value(internal_data.get(key))


def _string_value(value: JsonValue | None) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _text_digest(value: str) -> str:
    return f"sha256:{sha256(value.encode('utf-8')).hexdigest()}"


def _truncate_text(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
