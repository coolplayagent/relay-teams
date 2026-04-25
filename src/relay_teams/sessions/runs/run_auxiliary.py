# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable

from relay_teams.monitors import (
    MonitorAction,
    MonitorActionType,
    MonitorRule,
    MonitorService,
    MonitorSourceKind,
)
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.service import BackgroundTaskService
from relay_teams.sessions.runs.todo_service import TodoService


class RunAuxiliaryService:
    def __init__(
        self,
        *,
        get_monitor_service: Callable[[], MonitorService | None],
        get_background_task_manager: Callable[[], BackgroundTaskManager | None],
        get_background_task_service: Callable[[], BackgroundTaskService | None],
        get_todo_service: Callable[[], TodoService | None],
        get_run_session_id: Callable[[str], str],
        get_run_session_id_async: Callable[[str], Awaitable[str]],
    ) -> None:
        self._get_monitor_service = get_monitor_service
        self._get_background_task_manager = get_background_task_manager
        self._get_background_task_service = get_background_task_service
        self._get_todo_service = get_todo_service
        self._get_run_session_id = get_run_session_id
        self._get_run_session_id_async = get_run_session_id_async

    def create_monitor(
        self,
        *,
        run_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action_type: MonitorActionType,
        created_by_instance_id: str | None = None,
        created_by_role_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        service = self._require_monitor_service()
        normalized_source_key = source_key.strip()
        if source_kind == MonitorSourceKind.BACKGROUND_TASK:
            _ = self.get_background_task(
                run_id=run_id,
                background_task_id=normalized_source_key,
            )
        record = service.create_monitor(
            run_id=run_id,
            session_id=self._get_run_session_id(run_id),
            source_kind=source_kind,
            source_key=normalized_source_key,
            rule=rule,
            action=MonitorAction(action_type=action_type),
            created_by_instance_id=created_by_instance_id,
            created_by_role_id=created_by_role_id,
            tool_call_id=tool_call_id,
        )
        return record.model_dump(mode="json")

    async def create_monitor_async(
        self,
        *,
        run_id: str,
        source_kind: MonitorSourceKind,
        source_key: str,
        rule: MonitorRule,
        action_type: MonitorActionType,
        created_by_instance_id: str | None = None,
        created_by_role_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, object]:
        service = self._require_monitor_service()
        normalized_source_key = source_key.strip()
        if source_kind == MonitorSourceKind.BACKGROUND_TASK:
            _ = await self.get_background_task_async(
                run_id=run_id,
                background_task_id=normalized_source_key,
            )
        record = await service.create_monitor_async(
            run_id=run_id,
            session_id=await self._get_run_session_id_async(run_id),
            source_kind=source_kind,
            source_key=normalized_source_key,
            rule=rule,
            action=MonitorAction(action_type=action_type),
            created_by_instance_id=created_by_instance_id,
            created_by_role_id=created_by_role_id,
            tool_call_id=tool_call_id,
        )
        return record.model_dump(mode="json")

    def list_monitors(self, run_id: str) -> tuple[dict[str, object], ...]:
        service = self._require_monitor_service()
        return tuple(
            record.model_dump(mode="json") for record in service.list_for_run(run_id)
        )

    async def list_monitors_async(self, run_id: str) -> tuple[dict[str, object], ...]:
        service = self._require_monitor_service()
        return tuple(
            record.model_dump(mode="json")
            for record in await service.list_for_run_async(run_id)
        )

    def stop_monitor(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        service = self._require_monitor_service()
        return service.stop_for_run(
            run_id=run_id,
            monitor_id=monitor_id,
        ).model_dump(mode="json")

    async def stop_monitor_async(
        self,
        *,
        run_id: str,
        monitor_id: str,
    ) -> dict[str, object]:
        service = self._require_monitor_service()
        return (
            await service.stop_for_run_async(
                run_id=run_id,
                monitor_id=monitor_id,
            )
        ).model_dump(mode="json")

    def list_background_tasks(self, run_id: str) -> tuple[dict[str, object], ...]:
        service = self._get_background_task_service()
        if service is not None:
            return tuple(
                record.model_dump(mode="json")
                for record in service.list_for_run(run_id)
            )
        manager = self._get_background_task_manager()
        if manager is None:
            return ()
        return tuple(
            record.model_dump(mode="json")
            for record in manager.list_for_run(run_id)
            if record.execution_mode == "background"
        )

    async def list_background_tasks_async(
        self,
        run_id: str,
    ) -> tuple[dict[str, object], ...]:
        service = self._get_background_task_service()
        if service is not None:
            return tuple(
                record.model_dump(mode="json")
                for record in await service.list_for_run_async(run_id)
            )
        manager = self._get_background_task_manager()
        if manager is None:
            return ()
        return tuple(
            record.model_dump(mode="json")
            for record in await manager.list_for_run_async(run_id)
            if record.execution_mode == "background"
        )

    def get_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        service = self._get_background_task_service()
        if service is not None:
            return service.get_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            ).model_dump(mode="json")
        manager = self._get_background_task_manager()
        if manager is None:
            raise KeyError(f"Background task {background_task_id} not found")
        record = manager.get_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Background task {background_task_id} not found")
        return record.model_dump(mode="json")

    async def get_background_task_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        service = self._get_background_task_service()
        if service is not None:
            return (
                await service.get_for_run_async(
                    run_id=run_id,
                    background_task_id=background_task_id,
                )
            ).model_dump(mode="json")
        manager = self._get_background_task_manager()
        if manager is None:
            raise KeyError(f"Background task {background_task_id} not found")
        record = await manager.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Background task {background_task_id} not found")
        return record.model_dump(mode="json")

    def get_todo(self, run_id: str) -> dict[str, object]:
        service = self._get_todo_service()
        if service is None:
            raise RuntimeError("Todo service is not configured")
        snapshot = service.get_for_run(
            run_id=run_id,
            session_id=self._get_run_session_id(run_id),
        )
        return snapshot.model_dump(mode="json")

    async def get_todo_async(self, run_id: str) -> dict[str, object]:
        service = self._get_todo_service()
        if service is None:
            raise RuntimeError("Todo service is not configured")
        snapshot = await service.get_for_run_async(
            run_id=run_id,
            session_id=await self._get_run_session_id_async(run_id),
        )
        return snapshot.model_dump(mode="json")

    async def stop_background_task(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> dict[str, object]:
        service = self._get_background_task_service()
        if service is not None:
            record = await service.stop_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            )
            return record.model_dump(mode="json")
        manager = self._get_background_task_manager()
        if manager is None:
            raise KeyError(f"Background task {background_task_id} not found")
        record = manager.get_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Background task {background_task_id} not found")
        record = await manager.stop_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        return record.model_dump(mode="json")

    def _require_monitor_service(self) -> MonitorService:
        service = self._get_monitor_service()
        if service is None:
            raise RuntimeError("Monitor service is not configured")
        return service
