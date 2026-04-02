# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

from agent_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord

_MAX_SUMMARY_LENGTH = 500
_MAX_VISIBLE_OUTPUT_CHARS = 32_000
_OUTPUT_TRUNCATED_SUFFIX = "\n\n... output truncated; see log_path for full output ..."
_COMPLETION_FOLLOWUP_PREFIX = (
    "A managed background task finished. "
    "Respond to the user with one short status update based on the notification below.\n\n"
)


def build_background_task_payload(
    record: BackgroundTaskRecord,
) -> dict[str, JsonValue]:
    visible_output, output_truncated = _build_visible_output_excerpt(record)
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
        "output_excerpt": visible_output,
        "output_truncated": output_truncated,
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
    payload["output"] = payload["output_excerpt"]
    if not include_task_id:
        payload["background_task_id"] = None
    return payload


def build_background_task_completion_message(record: BackgroundTaskRecord) -> str:
    exit_code = "" if record.exit_code is None else str(record.exit_code)
    tool_call_id = record.tool_call_id or ""
    summary = _notification_summary(record)
    return (
        f"{_COMPLETION_FOLLOWUP_PREFIX}"
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


def _build_visible_output_excerpt(record: BackgroundTaskRecord) -> tuple[str, bool]:
    return _truncate_visible_output(record.output_excerpt)


def _truncate_visible_output(value: str) -> tuple[str, bool]:
    if len(value) <= _MAX_VISIBLE_OUTPUT_CHARS:
        return value, False
    available_chars = _MAX_VISIBLE_OUTPUT_CHARS - len(_OUTPUT_TRUNCATED_SUFFIX)
    if available_chars <= 0:
        return value[:_MAX_VISIBLE_OUTPUT_CHARS], True
    return value[:available_chars] + _OUTPUT_TRUNCATED_SUFFIX, True
