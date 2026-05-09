# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.logger import get_logger, log_event
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskStatus
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeStatus,
)

LOGGER = get_logger(__name__)


class SubagentLifecycleTerminalState(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_status: TaskStatus
    instance_status: InstanceStatus
    run_status: RunRuntimeStatus
    run_phase: RunRuntimePhase
    last_error: str | None
    task_result: str | None
    task_error: str | None


def terminal_state_for_background_status(
    status: BackgroundTaskStatus,
    *,
    output: str,
) -> SubagentLifecycleTerminalState:
    summarized_output = output.strip()
    if status == BackgroundTaskStatus.COMPLETED:
        return SubagentLifecycleTerminalState(
            task_status=TaskStatus.COMPLETED,
            instance_status=InstanceStatus.COMPLETED,
            run_status=RunRuntimeStatus.COMPLETED,
            run_phase=RunRuntimePhase.TERMINAL,
            last_error=None,
            task_result=summarized_output,
            task_error=None,
        )
    if status == BackgroundTaskStatus.STOPPED:
        return SubagentLifecycleTerminalState(
            task_status=TaskStatus.STOPPED,
            instance_status=InstanceStatus.STOPPED,
            run_status=RunRuntimeStatus.STOPPED,
            run_phase=RunRuntimePhase.IDLE,
            last_error="Task stopped by user",
            task_result=None,
            task_error="Task stopped by user",
        )
    return SubagentLifecycleTerminalState(
        task_status=TaskStatus.FAILED,
        instance_status=InstanceStatus.FAILED,
        run_status=RunRuntimeStatus.FAILED,
        run_phase=RunRuntimePhase.TERMINAL,
        last_error=summarized_output or "Task failed",
        task_result=None,
        task_error=summarized_output or "Task failed",
    )


def mark_subagent_terminal_records(
    *,
    update_task_status: Callable[..., object] | None,
    mark_instance_status: Callable[[str, InstanceStatus], object] | None,
    update_run_runtime: Callable[..., object] | None,
    subagent_run_id: str,
    session_id: str,
    task_id: str | None,
    instance_id: str | None,
    status: BackgroundTaskStatus,
    output: str,
) -> None:
    terminal = terminal_state_for_background_status(status, output=output)
    if task_id and update_task_status is not None:
        try:
            update_task_status(
                task_id,
                terminal.task_status,
                assigned_instance_id=instance_id,
                result=terminal.task_result,
                error_message=terminal.task_error,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="subagent_lifecycle.task_terminal_update_skipped",
                message="Failed to synchronize terminal subagent task status",
                payload={
                    "subagent_run_id": subagent_run_id,
                    "session_id": session_id,
                    "task_id": task_id,
                    "status": status.value,
                },
                exc_info=exc,
            )
    if instance_id and mark_instance_status is not None:
        try:
            mark_instance_status(instance_id, terminal.instance_status)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="subagent_lifecycle.instance_terminal_update_skipped",
                message="Failed to synchronize terminal subagent instance status",
                payload={
                    "subagent_run_id": subagent_run_id,
                    "session_id": session_id,
                    "instance_id": instance_id,
                    "status": status.value,
                },
                exc_info=exc,
            )
    if update_run_runtime is not None:
        try:
            update_run_runtime(
                subagent_run_id,
                status=terminal.run_status,
                phase=terminal.run_phase,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=terminal.last_error,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="subagent_lifecycle.runtime_terminal_update_skipped",
                message="Failed to synchronize terminal subagent runtime status",
                payload={
                    "subagent_run_id": subagent_run_id,
                    "session_id": session_id,
                    "status": status.value,
                },
                exc_info=exc,
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
