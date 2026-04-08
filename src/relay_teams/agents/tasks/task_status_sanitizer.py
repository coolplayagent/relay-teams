from __future__ import annotations

from collections.abc import Mapping

_ERROR_ONLY_STATUSES = {"failed", "stopped", "timeout"}


def sanitize_task_status_payload(value: object) -> object:
    if isinstance(value, list):
        return [sanitize_task_status_payload(item) for item in value]
    if not isinstance(value, dict):
        return value

    sanitized = {
        str(key): sanitize_task_status_payload(entry) for key, entry in value.items()
    }
    if not _looks_like_task_status_row(sanitized):
        return sanitized

    status = str(sanitized.get("status") or "").strip().lower()
    if status != "completed":
        sanitized.pop("result", None)
    if status not in _ERROR_ONLY_STATUSES:
        sanitized.pop("error", None)
        sanitized.pop("error_message", None)
    return sanitized


def _looks_like_task_status_row(value: Mapping[str, object]) -> bool:
    if "task_id" not in value or "status" not in value:
        return False
    return any(
        field in value
        for field in ("task_name", "role_id", "instance_id", "result", "error")
    )
