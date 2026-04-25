# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal, cast

import pytest

from relay_teams.monitors import (
    MonitorAction,
    MonitorActionType,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
)
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import BackgroundTaskRecord
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.run_auxiliary import RunAuxiliaryService
from relay_teams.sessions.runs.todo_models import TodoSnapshot
from relay_teams.sessions.runs.todo_service import TodoService


class _AsyncOnlyMonitorService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def create_monitor(
        self,
        *,
        run_id: str,
        session_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action: MonitorAction,
        created_by_instance_id: str | None,
        created_by_role_id: str | None,
        tool_call_id: str | None,
    ) -> MonitorSubscriptionRecord:
        _ = (
            run_id,
            session_id,
            source_kind,
            source_key,
            rule,
            action,
            created_by_instance_id,
            created_by_role_id,
            tool_call_id,
        )
        raise AssertionError("sync create_monitor must not run")

    async def create_monitor_async(
        self,
        *,
        run_id: str,
        session_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action: MonitorAction,
        created_by_instance_id: str | None,
        created_by_role_id: str | None,
        tool_call_id: str | None,
    ) -> MonitorSubscriptionRecord:
        _ = (
            rule,
            action,
            created_by_instance_id,
            created_by_role_id,
            tool_call_id,
        )
        self.calls.append(f"create:{source_key}")
        return MonitorSubscriptionRecord(
            monitor_id="mon_async",
            run_id=run_id,
            session_id=session_id,
            source_kind=source_kind,
            source_key=source_key,
        )

    def list_for_run(self, run_id: str) -> tuple[MonitorSubscriptionRecord, ...]:
        _ = run_id
        raise AssertionError("sync list_for_run must not run")

    async def list_for_run_async(
        self, run_id: str
    ) -> tuple[MonitorSubscriptionRecord, ...]:
        self.calls.append(f"list:{run_id}")
        return (
            MonitorSubscriptionRecord(
                monitor_id="mon_async",
                run_id=run_id,
                session_id="session-1",
                source_kind=MonitorSourceKind.BACKGROUND_TASK,
                source_key="background_task_1",
            ),
        )

    def stop_for_run(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> MonitorSubscriptionRecord:
        _ = (run_id, monitor_id)
        raise AssertionError("sync stop_for_run must not run")

    async def stop_for_run_async(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> MonitorSubscriptionRecord:
        self.calls.append(f"stop:{monitor_id}")
        return MonitorSubscriptionRecord(
            monitor_id=monitor_id,
            run_id=run_id,
            session_id="session-1",
            source_kind=MonitorSourceKind.BACKGROUND_TASK,
            source_key="background_task_1",
        )


class _AsyncOnlyBackgroundTaskService:
    def __init__(self, record: BackgroundTaskRecord) -> None:
        self.record = record
        self.calls: list[str] = []

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        _ = run_id
        raise AssertionError("sync list_for_run must not run")

    async def list_for_run_async(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        self.calls.append(f"list:{run_id}")
        return (self.record,)

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        _ = (run_id, background_task_id)
        raise AssertionError("sync get_for_run must not run")

    async def get_for_run_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        self.calls.append(f"get:{background_task_id}")
        if (
            self.record.run_id != run_id
            or self.record.background_task_id != background_task_id
        ):
            raise KeyError(background_task_id)
        return self.record


class _AsyncOnlyBackgroundTaskManager:
    def __init__(
        self,
        background_record: BackgroundTaskRecord,
        foreground_record: BackgroundTaskRecord,
    ) -> None:
        self.background_record = background_record
        self.foreground_record = foreground_record
        self.calls: list[str] = []

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        _ = run_id
        raise AssertionError("sync manager list_for_run must not run")

    async def list_for_run_async(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        self.calls.append(f"list:{run_id}")
        return (self.background_record, self.foreground_record)

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        _ = (run_id, background_task_id)
        raise AssertionError("sync manager get_for_run must not run")

    async def get_for_run_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        self.calls.append(f"get:{background_task_id}")
        if (
            self.background_record.run_id == run_id
            and self.background_record.background_task_id == background_task_id
        ):
            return self.background_record
        if (
            self.foreground_record.run_id == run_id
            and self.foreground_record.background_task_id == background_task_id
        ):
            return self.foreground_record
        raise KeyError(background_task_id)


class _AsyncOnlyTodoService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_for_run(
        self,
        *,
        run_id: str,
        session_id: str,
    ) -> TodoSnapshot:
        _ = (run_id, session_id)
        raise AssertionError("sync get_for_run must not run")

    async def get_for_run_async(
        self,
        *,
        run_id: str,
        session_id: str,
    ) -> TodoSnapshot:
        self.calls.append(f"get:{run_id}:{session_id}")
        return TodoSnapshot(run_id=run_id, session_id=session_id)


@pytest.mark.asyncio
async def test_run_auxiliary_monitor_async_uses_async_services() -> None:
    background_record = _background_record(
        background_task_id="background_task_1",
        execution_mode="background",
    )
    background_task_service = _AsyncOnlyBackgroundTaskService(background_record)
    monitor_service = _AsyncOnlyMonitorService()
    service = RunAuxiliaryService(
        get_monitor_service=lambda: cast(MonitorService, monitor_service),
        get_background_task_manager=lambda: None,
        get_background_task_service=lambda: cast(
            BackgroundTaskService, background_task_service
        ),
        get_todo_service=lambda: None,
        get_run_session_id=_fail_sync_session_id,
        get_run_session_id_async=_session_id_async,
    )

    created = await service.create_monitor_async(
        run_id="run-1",
        source_kind=MonitorSourceKind.BACKGROUND_TASK,
        source_key=" background_task_1 ",
        rule=MonitorRule(),
        action_type=MonitorActionType.WAKE_INSTANCE,
    )
    listed = await service.list_monitors_async("run-1")
    stopped = await service.stop_monitor_async(
        run_id="run-1",
        monitor_id="mon_async",
    )

    assert created["monitor_id"] == "mon_async"
    assert created["session_id"] == "session-for-run-1"
    assert listed[0]["monitor_id"] == "mon_async"
    assert stopped["monitor_id"] == "mon_async"
    assert background_task_service.calls == ["get:background_task_1"]
    assert monitor_service.calls == [
        "create:background_task_1",
        "list:run-1",
        "stop:mon_async",
    ]


@pytest.mark.asyncio
async def test_run_auxiliary_background_task_async_uses_manager_fallback() -> None:
    background_record = _background_record(
        background_task_id="background_task_1",
        execution_mode="background",
    )
    foreground_record = _background_record(
        background_task_id="foreground_task_1",
        execution_mode="foreground",
    )
    manager = _AsyncOnlyBackgroundTaskManager(
        background_record=background_record,
        foreground_record=foreground_record,
    )
    service = RunAuxiliaryService(
        get_monitor_service=lambda: None,
        get_background_task_manager=lambda: cast(BackgroundTaskManager, manager),
        get_background_task_service=lambda: None,
        get_todo_service=lambda: None,
        get_run_session_id=_fail_sync_session_id,
        get_run_session_id_async=_session_id_async,
    )

    listed = await service.list_background_tasks_async("run-1")
    fetched = await service.get_background_task_async(
        run_id="run-1",
        background_task_id="background_task_1",
    )

    assert tuple(item["background_task_id"] for item in listed) == (
        "background_task_1",
    )
    assert fetched["background_task_id"] == "background_task_1"
    assert manager.calls == ["list:run-1", "get:background_task_1"]


@pytest.mark.asyncio
async def test_run_auxiliary_todo_async_uses_async_session_resolver() -> None:
    todo_service = _AsyncOnlyTodoService()
    service = RunAuxiliaryService(
        get_monitor_service=lambda: None,
        get_background_task_manager=lambda: None,
        get_background_task_service=lambda: None,
        get_todo_service=lambda: cast(TodoService, todo_service),
        get_run_session_id=_fail_sync_session_id,
        get_run_session_id_async=_session_id_async,
    )

    snapshot = await service.get_todo_async("run-1")

    assert snapshot["session_id"] == "session-for-run-1"
    assert todo_service.calls == ["get:run-1:session-for-run-1"]


def _fail_sync_session_id(run_id: str) -> str:
    _ = run_id
    raise AssertionError("sync session resolver must not run")


async def _session_id_async(run_id: str) -> str:
    return f"session-for-{run_id}"


def _background_record(
    *,
    background_task_id: str,
    execution_mode: Literal["foreground", "background"],
) -> BackgroundTaskRecord:
    return BackgroundTaskRecord(
        background_task_id=background_task_id,
        run_id="run-1",
        session_id="session-1",
        instance_id="instance-1",
        role_id="role-1",
        tool_call_id="tool-call-1",
        command="sleep 1",
        cwd="/tmp/workspace",
        execution_mode=execution_mode,
        log_path="tmp/background_tasks/task.log",
    )
