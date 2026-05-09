# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable

from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeStatus,
)


def mark_subagent_resumed_records(
    *,
    update_task_status: Callable[..., object] | None,
    mark_instance_status: Callable[[str, InstanceStatus], object] | None,
    update_run_runtime: Callable[..., object] | None,
    run_id: str,
    task_id: str | None,
    instance_id: str,
    role_id: str,
) -> None:
    if task_id and update_task_status is not None:
        update_task_status(
            task_id,
            TaskStatus.ASSIGNED,
            assigned_instance_id=instance_id,
        )
    if mark_instance_status is not None:
        mark_instance_status(instance_id, InstanceStatus.RUNNING)
    if update_run_runtime is not None:
        update_run_runtime(
            run_id,
            status=RunRuntimeStatus.RUNNING,
            phase=RunRuntimePhase.SUBAGENT_RUNNING,
            active_instance_id=instance_id,
            active_task_id=task_id,
            active_role_id=role_id,
            active_subagent_instance_id=instance_id,
            last_error=None,
        )
