# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from agent_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord

_MAX_SUMMARY_LENGTH = 500


def build_background_task_payload(
    record: BackgroundTaskRecord,
) -> dict[str, JsonValue]:
    return {
        "background_task_id": record.background_task_id,
        "run_id": record.run_id,
        "session_id": record.session_id,
        "instance_id": record.instance_id,
        "role_id": record.role_id,
        "tool_call_id": record.tool_call_id,
        "command": record.command,
        "cwd": record.cwd,
        "status": record.status.value,
        "tty": record.tty,
        "timeout_ms": record.timeout_ms,
        "exit_code": record.exit_code,
        "recent_output": list(record.recent_output),
        "output_excerpt": record.output_excerpt,
        "log_path": record.log_path,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": (
            record.completed_at.isoformat() if record.completed_at is not None else None
        ),
        "completion_notified_at": (
            record.completion_notified_at.isoformat()
            if record.completion_notified_at is not None
            else None
        ),
    }


def build_background_task_result_payload(
    record: BackgroundTaskRecord,
    *,
    completed: bool,
    include_task_id: bool,
) -> dict[str, JsonValue]:
    payload = build_background_task_payload(record)
    payload["completed"] = completed
    payload["output"] = record.output_excerpt
    if not include_task_id:
        payload["background_task_id"] = None
    return payload


def build_background_task_completion_message(record: BackgroundTaskRecord) -> str:
    exit_code = "" if record.exit_code is None else str(record.exit_code)
    tool_call_id = record.tool_call_id or ""
    summary = _notification_summary(record)
    return (
        "<background-task-notification>\n"
        f"<background-task-id>{_xml_escape(record.background_task_id)}</background-task-id>\n"
        f"<tool-call-id>{_xml_escape(tool_call_id)}</tool-call-id>\n"
        f"<status>{_xml_escape(record.status.value)}</status>\n"
        f"<command>{_xml_escape(record.command)}</command>\n"
        f"<exit-code>{_xml_escape(exit_code)}</exit-code>\n"
        f"<log-path>{_xml_escape(record.log_path)}</log-path>\n"
        f"<summary>{_xml_escape(summary)}</summary>\n"
        "</background-task-notification>"
    )


def _notification_summary(record: BackgroundTaskRecord) -> str:
    if record.recent_output:
        summary = "\n".join(record.recent_output)
    else:
        summary = record.output_excerpt
    summary = summary.strip()
    if len(summary) <= _MAX_SUMMARY_LENGTH:
        return summary
    return summary[: _MAX_SUMMARY_LENGTH - 3] + "..."


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
