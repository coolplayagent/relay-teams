from __future__ import annotations

from pydantic import JsonValue

from collections.abc import Mapping

from agent_teams.agents.tasks.enums import TaskStatus
from agent_teams.agents.tasks.models import TaskRecord


def build_task_status_snapshot(
    *,
    tasks: Mapping[str, Mapping[str, object]],
    records: Mapping[str, TaskRecord],
) -> dict[str, dict[str, JsonValue]]:
    return {
        task_name: build_task_status_row(
            task_name=task_name,
            task_id=str(task_info.get("task_id", "")),
            role_id=str(task_info.get("role_id", "")),
            record=records.get(str(task_info.get("task_id", ""))),
        )
        for task_name, task_info in tasks.items()
    }


def build_task_status_row(
    *,
    task_name: str,
    task_id: str,
    role_id: str,
    record: TaskRecord | None,
) -> dict[str, JsonValue]:
    if record is None:
        return {
            "task_name": task_name,
            "task_id": task_id,
            "role_id": role_id,
            "instance_id": "",
            "status": "missing",
        }

    row: dict[str, JsonValue] = {
        "task_name": task_name,
        "task_id": task_id,
        "role_id": role_id,
        "instance_id": record.assigned_instance_id or "",
        "status": record.status.value,
    }
    if record.result:
        row["result"] = record.result
    if record.error_message and record.status in {
        TaskStatus.FAILED,
        TaskStatus.STOPPED,
        TaskStatus.TIMEOUT,
    }:
        row["error"] = record.error_message
    return row
