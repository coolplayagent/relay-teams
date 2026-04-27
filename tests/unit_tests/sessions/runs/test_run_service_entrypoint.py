# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.run_recovery import AutoRecoveryReason
from relay_teams.sessions.runs.run_followups import RunFollowupRouter
from relay_teams.sessions.runs.run_service import SessionRunService


class _CapturingFollowupRouter:
    def __init__(self) -> None:
        self.records: list[BackgroundTaskRecord] = []
        self.messages: list[str] = []

    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        self.records.append(record)
        self.messages.append(message)


def test_session_run_service_is_available_from_explicit_module() -> None:
    assert SessionRunService.__name__ == "SessionRunService"


def test_auto_recovery_types_remain_available_from_recovery_module() -> None:
    assert AutoRecoveryReason.NETWORK_TIMEOUT.value == "auto_recovery_network_timeout"


def test_background_task_completion_is_not_delegated_to_event_loop() -> None:
    service = cast(
        SessionRunService,
        SessionRunService.__new__(SessionRunService),
    )
    router = _CapturingFollowupRouter()
    object.__setattr__(service, "_followup_router", cast(RunFollowupRouter, router))
    object.__setattr__(service, "_event_loop", object())
    object.__setattr__(service, "_call_in_bound_loop", _raise_if_delegated)
    record = BackgroundTaskRecord(
        background_task_id="background-task-1",
        run_id="run-1",
        session_id="session-1",
        instance_id="instance-1",
        role_id="role-1",
        tool_call_id="tool-call-1",
        command="subagent:Explorer",
        cwd="workspace-1",
        execution_mode="background",
        status=BackgroundTaskStatus.COMPLETED,
        exit_code=0,
        recent_output=("done",),
        output_excerpt="done",
        log_path="",
    )

    service.handle_background_task_completion(record=record, message="done")

    assert router.records == [record]
    assert router.messages == ["done"]


def _raise_if_delegated(_: Callable[[], object]) -> object:
    raise AssertionError("background task completion must not run on the event loop")
