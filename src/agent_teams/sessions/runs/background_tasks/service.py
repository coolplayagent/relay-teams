# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Protocol

from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from agent_teams.tools.workspace_tools.shell_executor import normalize_timeout
from agent_teams.workspace import WorkspaceHandle

LOGGER = get_logger(__name__)
_DEFAULT_SYNC_WAIT_MS = 1000
_MIN_SYNC_WAIT_MS = 200
_MAX_SUMMARY_LENGTH = 500


class BackgroundTaskCompletionSink(Protocol):
    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None: ...


class BackgroundTaskService:
    def __init__(
        self,
        *,
        background_task_manager: BackgroundTaskManager | None,
        repository: BackgroundTaskRepository,
    ) -> None:
        self._background_task_manager = background_task_manager
        self._repository = repository
        self._completion_sink: BackgroundTaskCompletionSink | None = None
        if self._background_task_manager is not None:
            self._background_task_manager.set_completion_listener(
                self._handle_background_task_completion
            )

    def bind_completion_sink(
        self,
        sink: BackgroundTaskCompletionSink | None,
    ) -> None:
        self._completion_sink = sink

    async def run_shell(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace: WorkspaceHandle,
        command: str,
        cwd: Path,
        yield_time_ms: int | None,
        timeout_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
        background: bool,
    ) -> tuple[BackgroundTaskRecord, bool]:
        manager = self._require_manager()
        timeout = normalize_timeout(timeout_ms)
        if background:
            record = await manager.start_session(
                run_id=run_id,
                session_id=session_id,
                instance_id=instance_id,
                role_id=role_id,
                tool_call_id=tool_call_id,
                workspace=workspace,
                command=command,
                cwd=cwd,
                timeout_ms=timeout,
                env=env,
                tty=tty,
                execution_mode="background",
            )
            updated, completed = await manager.interact_for_run(
                run_id=run_id,
                exec_session_id=record.exec_session_id,
                chars="",
                yield_time_ms=yield_time_ms,
                is_initial_poll=True,
            )
            return updated, completed

        record = await manager.start_session(
            run_id=run_id,
            session_id=session_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_call_id=tool_call_id,
            workspace=workspace,
            command=command,
            cwd=cwd,
            timeout_ms=timeout,
            env=env,
            tty=tty,
            execution_mode="foreground",
        )
        wait_ms = _normalize_sync_wait_ms(yield_time_ms)
        while True:
            updated, completed = await manager.wait_for_run(
                run_id=run_id,
                exec_session_id=record.exec_session_id,
                wait_ms=wait_ms,
            )
            if completed:
                return updated, True

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        manager = self._require_manager()
        return tuple(
            record
            for record in manager.list_for_run(run_id)
            if record.execution_mode == "background"
        )

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = self._require_manager().get_for_run(
            run_id=run_id,
            exec_session_id=background_task_id,
        )
        if record.execution_mode != "background":
            raise KeyError(f"Unknown background task: {background_task_id}")
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        wait_ms: int,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        if not record.is_active:
            return self._mark_completion_consumed(record), True
        updated, completed = await self._require_manager().wait_for_run(
            run_id=run_id,
            exec_session_id=background_task_id,
            wait_ms=wait_ms,
        )
        if not completed:
            return updated, False
        return self._mark_completion_consumed(updated), True

    async def stop_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        _ = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        return await self._require_manager().stop_for_run(
            run_id=run_id,
            exec_session_id=background_task_id,
        )

    async def _handle_background_task_completion(
        self, record: BackgroundTaskRecord
    ) -> None:
        await asyncio.sleep(0)
        latest = self._repository.get(record.exec_session_id)
        current = latest or record
        if current.execution_mode != "background":
            return
        if current.status == BackgroundTaskStatus.STOPPED:
            return
        if current.completion_notified_at is not None:
            return
        if self._completion_sink is None:
            return
        message = _build_completion_message(current)
        try:
            self._completion_sink.handle_background_task_completion(
                record=current,
                message=message,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                event="background_task.notification_failed",
                message="Failed to deliver background task completion notification",
                payload={"background_task_id": current.exec_session_id},
                exc_info=exc,
            )
            return
        _ = self._mark_completion_consumed(current)

    def _mark_completion_consumed(
        self, record: BackgroundTaskRecord
    ) -> BackgroundTaskRecord:
        if (
            record.execution_mode != "background"
            or record.is_active
            or record.status == BackgroundTaskStatus.STOPPED
            or record.completion_notified_at is not None
        ):
            return record
        completed_at = datetime.now(tz=timezone.utc)
        return self._repository.upsert(
            record.model_copy(
                update={
                    "completion_notified_at": completed_at,
                    "updated_at": completed_at,
                }
            )
        )

    def _require_manager(self) -> BackgroundTaskManager:
        if self._background_task_manager is None:
            raise RuntimeError("Background task service is not configured")
        return self._background_task_manager


def _normalize_sync_wait_ms(wait_ms: int | None) -> int:
    if wait_ms is None:
        return _DEFAULT_SYNC_WAIT_MS
    if wait_ms < 1:
        raise ValueError("yield_time_ms must be >= 1")
    return max(_MIN_SYNC_WAIT_MS, wait_ms)


def _build_completion_message(record: BackgroundTaskRecord) -> str:
    exit_code = "" if record.exit_code is None else str(record.exit_code)
    tool_call_id = record.tool_call_id or ""
    summary = _notification_summary(record)
    return (
        "<background-task-notification>\n"
        f"<background-task-id>{_xml_escape(record.exec_session_id)}</background-task-id>\n"
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
