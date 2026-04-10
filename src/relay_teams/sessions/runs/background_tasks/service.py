# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from relay_teams.agents.orchestration.task_execution_service import (
    TaskExecutionResult,
)
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.instances.models import (
    SubAgentInstance,
    create_subagent_instance,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import TaskEnvelope, VerificationPlan
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    normalize_timeout,
)
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.projection import (
    build_background_task_completion_message,
)
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.enums import ExecutionMode, RunEventType
from relay_teams.sessions.runs.event_stream import RunEventHub
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.run_models import (
    IntentInput,
    RunEvent,
    RunThinkingConfig,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeStatus,
)
from relay_teams.sessions.session_models import SessionMode
from relay_teams.workspace import WorkspaceHandle

LOGGER = get_logger(__name__)
_COMPLETION_RETRY_INITIAL_DELAY_SECONDS = 1.0
_COMPLETION_RETRY_MAX_DELAY_SECONDS = 30.0
_SUBAGENT_COMMAND_PREFIX = "subagent:"


class BackgroundTaskCompletionSink(Protocol):
    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None: ...


class _BackgroundTaskExecutor(Protocol):
    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> TaskExecutionResult: ...


class _BackgroundTaskRunController(Protocol):
    def register_run_task(
        self,
        *,
        run_id: str,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None: ...

    def unregister_run_task(self, run_id: str) -> None: ...

    def request_run_stop(self, run_id: str) -> bool: ...

    def is_run_stop_requested(self, run_id: str) -> bool: ...


class _BackgroundTaskRunRuntimeRepository(Protocol):
    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str | None = None,
        status: RunRuntimeStatus = RunRuntimeStatus.QUEUED,
        phase: RunRuntimePhase = RunRuntimePhase.IDLE,
    ) -> object: ...

    def get(self, run_id: str) -> object | None: ...

    def update(self, run_id: str, **changes: object) -> object: ...


class _BackgroundTaskAgentRepository(Protocol):
    def upsert_instance(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        workspace_id: str,
        conversation_id: str | None = None,
        status: InstanceStatus,
    ) -> None: ...


class _BackgroundTaskTaskRepository(Protocol):
    def create(self, envelope: TaskEnvelope) -> object: ...

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None: ...


class _BackgroundTaskIntentRepository(Protocol):
    def get(
        self,
        run_id: str,
        *,
        fallback_session_id: str | None = None,
    ) -> IntentInput: ...

    def upsert(self, *, run_id: str, session_id: str, intent: IntentInput) -> None: ...


class _ManagedSubagentTaskRuntime:
    def __init__(
        self,
        *,
        worker_task: asyncio.Task[None],
        subagent_run_id: str,
    ) -> None:
        self.worker_task = worker_task
        self.subagent_run_id = subagent_run_id


class SynchronousSubagentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    title: str = ""
    output: str = ""


class _PreparedSubagentLaunch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        frozen=True,
    )

    normalized_prompt: str = Field(min_length=1)
    normalized_title: str = Field(min_length=1)
    subagent_run_id: str = Field(min_length=1)
    subagent_role_id: str = Field(min_length=1)
    subagent_instance: SubAgentInstance
    subagent_task: TaskEnvelope


class BackgroundTaskService:
    def __init__(
        self,
        *,
        background_task_manager: BackgroundTaskManager | None,
        repository: BackgroundTaskRepository,
        run_event_hub: RunEventHub | None = None,
        task_execution_service: _BackgroundTaskExecutor | None = None,
        agent_repo: _BackgroundTaskAgentRepository | None = None,
        task_repo: _BackgroundTaskTaskRepository | None = None,
        run_intent_repo: _BackgroundTaskIntentRepository | None = None,
        run_control_manager: _BackgroundTaskRunController | None = None,
        run_runtime_repo: _BackgroundTaskRunRuntimeRepository | None = None,
    ) -> None:
        self._background_task_manager = background_task_manager
        self._repository = repository
        self._run_event_hub = run_event_hub
        self._task_execution_service = task_execution_service
        self._agent_repo = agent_repo
        self._task_repo = task_repo
        self._run_intent_repo = run_intent_repo
        self._run_control_manager = run_control_manager
        self._run_runtime_repo = run_runtime_repo
        self._completion_sink: BackgroundTaskCompletionSink | None = None
        self._completion_retry_tasks: dict[str, asyncio.Task[None]] = {}
        self._subagent_runtimes: dict[str, _ManagedSubagentTaskRuntime] = {}
        if self._background_task_manager is not None:
            self._background_task_manager.set_completion_listener(
                self._handle_background_task_completion
            )

    def bind_completion_sink(
        self,
        sink: BackgroundTaskCompletionSink | None,
    ) -> None:
        self._completion_sink = sink
        if sink is not None:
            self._flush_pending_completion_notifications()

    def replace_subagent_runtime_dependencies(
        self,
        *,
        task_execution_service: _BackgroundTaskExecutor | None,
        agent_repo: _BackgroundTaskAgentRepository | None,
        task_repo: _BackgroundTaskTaskRepository | None,
        run_intent_repo: _BackgroundTaskIntentRepository | None,
        run_control_manager: _BackgroundTaskRunController | None,
        run_runtime_repo: _BackgroundTaskRunRuntimeRepository | None,
    ) -> None:
        self._task_execution_service = task_execution_service
        self._agent_repo = agent_repo
        self._task_repo = task_repo
        self._run_intent_repo = run_intent_repo
        self._run_control_manager = run_control_manager
        self._run_runtime_repo = run_runtime_repo

    async def execute_command(
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
        timeout = (
            None if background and timeout_ms is None else normalize_timeout(timeout_ms)
        )
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
                background_task_id=record.background_task_id,
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
        return await manager.wait_for_run(
            run_id=run_id,
            background_task_id=record.background_task_id,
        )

    async def start_subagent(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace_id: str,
        cwd: Path,
        subagent_role_id: str,
        title: str,
        prompt: str,
    ) -> BackgroundTaskRecord:
        task_execution_service = self._require_task_execution_service()
        run_control_manager = self._require_run_control_manager()
        background_task_id = f"background_task_{uuid4().hex[:12]}"
        prepared = self._prepare_subagent_launch(
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            subagent_role_id=subagent_role_id,
            title=title,
            prompt=prompt,
        )
        record = self._repository.upsert(
            BackgroundTaskRecord(
                background_task_id=background_task_id,
                run_id=run_id,
                session_id=session_id,
                kind=BackgroundTaskKind.SUBAGENT,
                instance_id=instance_id,
                role_id=role_id,
                tool_call_id=tool_call_id,
                title=prepared.normalized_title,
                command=f"{_SUBAGENT_COMMAND_PREFIX}{prepared.subagent_role_id}",
                cwd=str(cwd),
                execution_mode="background",
                status=BackgroundTaskStatus.RUNNING,
                tty=False,
                timeout_ms=None,
                log_path="",
                subagent_role_id=prepared.subagent_role_id,
                subagent_run_id=prepared.subagent_run_id,
                subagent_task_id=prepared.subagent_task.task_id,
                subagent_instance_id=prepared.subagent_instance.instance_id,
            )
        )

        async def run_worker() -> None:
            try:
                result = await task_execution_service.execute(
                    instance_id=prepared.subagent_instance.instance_id,
                    role_id=prepared.subagent_role_id,
                    task=prepared.subagent_task,
                    user_prompt_override=prepared.normalized_prompt,
                )
            except asyncio.CancelledError:
                stopped = run_control_manager.is_run_stop_requested(
                    prepared.subagent_run_id
                )
                await self._finalize_subagent_record(
                    background_task_id=background_task_id,
                    status=(
                        BackgroundTaskStatus.STOPPED
                        if stopped
                        else BackgroundTaskStatus.FAILED
                    ),
                    exit_code=None if stopped else 1,
                    output="Task cancelled",
                )
                return
            except Exception as exc:
                await self._finalize_subagent_record(
                    background_task_id=background_task_id,
                    status=BackgroundTaskStatus.FAILED,
                    exit_code=1,
                    output=str(exc),
                )
                return
            status, exit_code = _status_from_execution_result(result)
            await self._finalize_subagent_record(
                background_task_id=background_task_id,
                status=status,
                exit_code=exit_code,
                output=result.output,
            )

        worker_task = asyncio.create_task(run_worker())
        self._subagent_runtimes[background_task_id] = _ManagedSubagentTaskRuntime(
            worker_task=worker_task,
            subagent_run_id=prepared.subagent_run_id,
        )
        run_control_manager.register_run_task(
            run_id=prepared.subagent_run_id,
            session_id=session_id,
            task=worker_task,
        )
        self._publish_background_task_event(
            event_type=RunEventType.BACKGROUND_TASK_STARTED,
            record=record,
        )
        return record

    async def run_subagent(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        subagent_role_id: str,
        title: str,
        prompt: str,
    ) -> SynchronousSubagentResult:
        task_execution_service = self._require_task_execution_service()
        run_control_manager = self._require_run_control_manager()
        prepared = self._prepare_subagent_launch(
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            subagent_role_id=subagent_role_id,
            title=title,
            prompt=prompt,
        )

        result_holder: dict[str, SynchronousSubagentResult] = {}

        async def run_worker() -> None:
            try:
                result = await task_execution_service.execute(
                    instance_id=prepared.subagent_instance.instance_id,
                    role_id=prepared.subagent_role_id,
                    task=prepared.subagent_task,
                    user_prompt_override=prepared.normalized_prompt,
                )
            except asyncio.CancelledError:
                stopped = run_control_manager.is_run_stop_requested(
                    prepared.subagent_run_id
                )
                self._finalize_subagent_run_runtime(
                    subagent_run_id=prepared.subagent_run_id,
                    status=(
                        BackgroundTaskStatus.STOPPED
                        if stopped
                        else BackgroundTaskStatus.FAILED
                    ),
                    output="Task cancelled",
                )
                raise
            except Exception as exc:
                output = str(exc)
                self._finalize_subagent_run_runtime(
                    subagent_run_id=prepared.subagent_run_id,
                    status=BackgroundTaskStatus.FAILED,
                    output=output,
                )
                raise RuntimeError(output) from exc

            status, _ = _status_from_execution_result(result)
            output = result.output.strip()
            self._finalize_subagent_run_runtime(
                subagent_run_id=prepared.subagent_run_id,
                status=status,
                output=output,
            )
            if status != BackgroundTaskStatus.COMPLETED:
                message = output or result.error_message or "Subagent failed"
                raise RuntimeError(message)
            result_holder["result"] = SynchronousSubagentResult(
                run_id=prepared.subagent_run_id,
                instance_id=prepared.subagent_instance.instance_id,
                role_id=prepared.subagent_role_id,
                task_id=prepared.subagent_task.task_id,
                title=prepared.normalized_title,
                output=output,
            )

        worker_task = asyncio.create_task(run_worker())
        run_control_manager.register_run_task(
            run_id=prepared.subagent_run_id,
            session_id=session_id,
            task=worker_task,
        )
        try:
            await asyncio.shield(worker_task)
        except asyncio.CancelledError:
            run_control_manager.request_run_stop(prepared.subagent_run_id)
            await asyncio.gather(worker_task, return_exceptions=True)
            raise
        finally:
            run_control_manager.unregister_run_task(prepared.subagent_run_id)
        result = result_holder.get("result")
        if result is None:
            raise RuntimeError("Subagent completed without returning a result")
        return result

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        return tuple(
            record
            for record in self._repository.list_by_run(run_id)
            if record.execution_mode == "background"
        )

    def get_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = self._repository.get(background_task_id)
        if (
            record is None
            or record.run_id != run_id
            or record.execution_mode != "background"
        ):
            raise KeyError(f"Unknown background task: {background_task_id}")
        return record

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> tuple[BackgroundTaskRecord, bool]:
        record = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        if not record.is_active:
            return self._mark_completion_consumed(record), True
        if record.kind == BackgroundTaskKind.SUBAGENT:
            runtime = self._subagent_runtimes.get(background_task_id)
            if runtime is None:
                refreshed = self.get_for_run(
                    run_id=run_id,
                    background_task_id=background_task_id,
                )
                if not refreshed.is_active:
                    return self._mark_completion_consumed(refreshed), True
                return refreshed, False
            await asyncio.shield(runtime.worker_task)
            updated = self.get_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            )
            return self._mark_completion_consumed(updated), True
        updated, completed = await self._require_manager().wait_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
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
        record = self.get_for_run(run_id=run_id, background_task_id=background_task_id)
        if record.kind == BackgroundTaskKind.SUBAGENT:
            runtime = self._subagent_runtimes.get(background_task_id)
            if runtime is None:
                if record.is_active:
                    return await self._finalize_subagent_record(
                        background_task_id=background_task_id,
                        status=BackgroundTaskStatus.STOPPED,
                        exit_code=None,
                        output="Task stopped",
                    )
                return record
            self._require_run_control_manager().request_run_stop(
                runtime.subagent_run_id
            )
            await asyncio.gather(runtime.worker_task, return_exceptions=True)
            updated = self.get_for_run(
                run_id=run_id,
                background_task_id=background_task_id,
            )
            if updated.is_active:
                return await self._finalize_subagent_record(
                    background_task_id=background_task_id,
                    status=BackgroundTaskStatus.STOPPED,
                    exit_code=None,
                    output="Task stopped",
                )
            return updated
        return await self._require_manager().stop_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )

    async def _finalize_subagent_record(
        self,
        *,
        background_task_id: str,
        status: BackgroundTaskStatus,
        exit_code: int | None,
        output: str,
    ) -> BackgroundTaskRecord:
        current = self._repository.get(background_task_id)
        if current is None:
            raise KeyError(f"Unknown background task: {background_task_id}")
        completed_at = datetime.now(tz=timezone.utc)
        summarized_output = output.strip()
        record = self._repository.upsert(
            current.model_copy(
                update={
                    "status": status,
                    "exit_code": exit_code,
                    "recent_output": _recent_output_lines(summarized_output),
                    "output_excerpt": summarized_output,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                }
            )
        )
        runtime = self._subagent_runtimes.pop(background_task_id, None)
        if runtime is not None:
            self._finalize_subagent_run_runtime(
                subagent_run_id=runtime.subagent_run_id,
                status=status,
                output=summarized_output,
            )
            self._require_run_control_manager().unregister_run_task(
                runtime.subagent_run_id
            )
        self._publish_background_task_event(
            event_type=(
                RunEventType.BACKGROUND_TASK_STOPPED
                if status == BackgroundTaskStatus.STOPPED
                else RunEventType.BACKGROUND_TASK_COMPLETED
            ),
            record=record,
        )
        if status != BackgroundTaskStatus.STOPPED:
            await self._handle_background_task_completion(record)
        return record

    async def _handle_background_task_completion(
        self, record: BackgroundTaskRecord
    ) -> None:
        await asyncio.sleep(0)
        delivered = self._attempt_completion_delivery(record.background_task_id)
        if not delivered and self._completion_sink is not None:
            self._schedule_completion_retry(record.background_task_id)

    def _attempt_completion_delivery(self, background_task_id: str) -> bool:
        current = self._repository.get(background_task_id)
        if current is None:
            return True
        if not self._should_notify_completion(current):
            return True
        if self._completion_sink is None:
            return False
        message = build_background_task_completion_message(current)
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
                payload={"background_task_id": current.background_task_id},
                exc_info=exc,
            )
            return False
        _ = self._mark_completion_consumed(current)
        return True

    def _flush_pending_completion_notifications(self) -> None:
        for record in self._repository.list_all():
            if self._should_notify_completion(record):
                delivered = self._attempt_completion_delivery(record.background_task_id)
                if not delivered and self._completion_sink is not None:
                    self._schedule_completion_retry(
                        record.background_task_id,
                        initial_delay_seconds=0.0,
                    )

    def _schedule_completion_retry(
        self,
        background_task_id: str,
        *,
        initial_delay_seconds: float = _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
    ) -> None:
        existing = self._completion_retry_tasks.get(background_task_id)
        if existing is not None and not existing.done():
            return
        try:
            self._completion_retry_tasks[background_task_id] = asyncio.create_task(
                self._retry_completion_delivery(
                    background_task_id,
                    initial_delay_seconds=initial_delay_seconds,
                )
            )
        except RuntimeError:
            return

    async def _retry_completion_delivery(
        self,
        background_task_id: str,
        *,
        initial_delay_seconds: float,
    ) -> None:
        delay_seconds = initial_delay_seconds
        try:
            while True:
                if delay_seconds > 0:
                    await asyncio.sleep(delay_seconds)
                if self._attempt_completion_delivery(background_task_id):
                    return
                if self._completion_sink is None:
                    return
                delay_seconds = min(
                    _COMPLETION_RETRY_MAX_DELAY_SECONDS,
                    max(
                        _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
                        delay_seconds * 2
                        if delay_seconds > 0
                        else _COMPLETION_RETRY_INITIAL_DELAY_SECONDS,
                    ),
                )
        finally:
            task = self._completion_retry_tasks.get(background_task_id)
            if task is asyncio.current_task():
                self._completion_retry_tasks.pop(background_task_id, None)

    def _mark_completion_consumed(
        self, record: BackgroundTaskRecord
    ) -> BackgroundTaskRecord:
        if not self._should_notify_completion(record):
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

    def _should_notify_completion(self, record: BackgroundTaskRecord) -> bool:
        return (
            record.execution_mode == "background"
            and not record.is_active
            and record.status != BackgroundTaskStatus.STOPPED
            and record.completion_notified_at is None
        )

    def _publish_background_task_event(
        self,
        *,
        event_type: RunEventType,
        record: BackgroundTaskRecord,
    ) -> None:
        if self._run_event_hub is None:
            return
        self._run_event_hub.publish(
            RunEvent(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=event_type,
                payload_json=record.model_dump_json(),
            )
        )

    def _upsert_subagent_intent(
        self,
        *,
        parent_run_id: str,
        subagent_run_id: str,
        session_id: str,
        subagent_role_id: str,
        prompt: str,
    ) -> None:
        if self._run_intent_repo is None:
            return
        parent_thinking = RunThinkingConfig()
        parent_yolo = False
        parent_conversation_context = None
        try:
            parent_intent = self._run_intent_repo.get(parent_run_id)
        except KeyError:
            parent_intent = None
        if parent_intent is not None:
            parent_thinking = parent_intent.thinking
            parent_yolo = parent_intent.yolo
            parent_conversation_context = parent_intent.conversation_context
        self._run_intent_repo.upsert(
            run_id=subagent_run_id,
            session_id=session_id,
            intent=IntentInput(
                session_id=session_id,
                input=content_parts_from_text(prompt),
                execution_mode=ExecutionMode.AI,
                yolo=parent_yolo,
                reuse_root_instance=False,
                thinking=parent_thinking,
                target_role_id=subagent_role_id,
                session_mode=SessionMode.NORMAL,
                conversation_context=parent_conversation_context,
            ),
        )

    def _prepare_subagent_launch(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        subagent_role_id: str,
        title: str,
        prompt: str,
    ) -> _PreparedSubagentLaunch:
        agent_repo = self._require_agent_repo()
        task_repo = self._require_task_repo()

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise ValueError("prompt must not be empty")
        if self._run_intent_repo is not None:
            try:
                parent_intent = self._run_intent_repo.get(run_id)
            except KeyError:
                parent_intent = None
            if (
                parent_intent is not None
                and parent_intent.session_mode != SessionMode.NORMAL
            ):
                raise ValueError(
                    "spawn_subagent is only available for normal-mode runs"
                )
        normalized_title = title.strip() or _default_subagent_title(
            role_id=subagent_role_id,
            prompt=normalized_prompt,
        )
        subagent_run_id = f"subagent_run_{uuid4().hex[:12]}"
        subagent_instance = create_subagent_instance(
            subagent_role_id,
            session_id=session_id,
            workspace_id=workspace_id,
        )
        subagent_task = TaskEnvelope(
            task_id=new_task_id().value,
            session_id=session_id,
            parent_task_id=None,
            trace_id=subagent_run_id,
            role_id=subagent_role_id,
            title=normalized_title,
            objective=normalized_prompt,
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
        agent_repo.upsert_instance(
            run_id=subagent_run_id,
            trace_id=subagent_run_id,
            session_id=session_id,
            instance_id=subagent_instance.instance_id,
            role_id=subagent_role_id,
            workspace_id=workspace_id,
            conversation_id=subagent_instance.conversation_id,
            status=InstanceStatus.IDLE,
        )
        task_repo.create(subagent_task)
        task_repo.update_status(
            subagent_task.task_id,
            TaskStatus.ASSIGNED,
            assigned_instance_id=subagent_instance.instance_id,
        )
        self._upsert_subagent_intent(
            parent_run_id=run_id,
            subagent_run_id=subagent_run_id,
            session_id=session_id,
            subagent_role_id=subagent_role_id,
            prompt=normalized_prompt,
        )
        if self._run_runtime_repo is not None:
            _ = self._run_runtime_repo.ensure(
                run_id=subagent_run_id,
                session_id=session_id,
                root_task_id=subagent_task.task_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.SUBAGENT_RUNNING,
            )
            _ = self._run_runtime_repo.update(
                subagent_run_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.SUBAGENT_RUNNING,
                active_instance_id=subagent_instance.instance_id,
                active_task_id=subagent_task.task_id,
                active_role_id=subagent_role_id,
                active_subagent_instance_id=subagent_instance.instance_id,
                last_error=None,
            )
        return _PreparedSubagentLaunch(
            normalized_prompt=normalized_prompt,
            normalized_title=normalized_title,
            subagent_run_id=subagent_run_id,
            subagent_role_id=subagent_role_id,
            subagent_instance=subagent_instance,
            subagent_task=subagent_task,
        )

    def _require_manager(self) -> BackgroundTaskManager:
        if self._background_task_manager is None:
            raise RuntimeError("Background task service is not configured")
        return self._background_task_manager

    def _require_task_execution_service(self) -> _BackgroundTaskExecutor:
        if self._task_execution_service is None:
            raise RuntimeError("Background subagent execution is not configured")
        return self._task_execution_service

    def _require_agent_repo(self) -> _BackgroundTaskAgentRepository:
        if self._agent_repo is None:
            raise RuntimeError("Background subagent agent_repo is not configured")
        return self._agent_repo

    def _require_task_repo(self) -> _BackgroundTaskTaskRepository:
        if self._task_repo is None:
            raise RuntimeError("Background subagent task_repo is not configured")
        return self._task_repo

    def _require_run_control_manager(self) -> _BackgroundTaskRunController:
        if self._run_control_manager is None:
            raise RuntimeError("Background subagent run control is not configured")
        return self._run_control_manager

    def _finalize_subagent_run_runtime(
        self,
        *,
        subagent_run_id: str,
        status: BackgroundTaskStatus,
        output: str,
    ) -> None:
        run_runtime_repo = self._run_runtime_repo
        if run_runtime_repo is None:
            return
        runtime = run_runtime_repo.get(subagent_run_id)
        if runtime is None:
            return
        if status == BackgroundTaskStatus.COMPLETED:
            run_runtime_repo.update(
                subagent_run_id,
                status=RunRuntimeStatus.COMPLETED,
                phase=RunRuntimePhase.TERMINAL,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error=None,
            )
            return
        if status == BackgroundTaskStatus.STOPPED:
            run_runtime_repo.update(
                subagent_run_id,
                status=RunRuntimeStatus.STOPPED,
                phase=RunRuntimePhase.IDLE,
                active_instance_id=None,
                active_task_id=None,
                active_role_id=None,
                active_subagent_instance_id=None,
                last_error="Task stopped by user",
            )
            return
        run_runtime_repo.update(
            subagent_run_id,
            status=RunRuntimeStatus.FAILED,
            phase=RunRuntimePhase.TERMINAL,
            active_instance_id=None,
            active_task_id=None,
            active_role_id=None,
            active_subagent_instance_id=None,
            last_error=output or "Task failed",
        )


def _default_subagent_title(*, role_id: str, prompt: str) -> str:
    summary = " ".join(prompt.split())
    if not summary:
        return role_id
    return f"{role_id}: {summary[:80]}"


def _recent_output_lines(output: str) -> tuple[str, ...]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ()
    return tuple(lines[-3:])


def _status_from_execution_result(
    result: TaskExecutionResult,
) -> tuple[BackgroundTaskStatus, int | None]:
    completion_reason = getattr(
        result.completion_reason, "value", result.completion_reason
    )
    if completion_reason == RunCompletionReason.ASSISTANT_RESPONSE.value:
        return BackgroundTaskStatus.COMPLETED, 0
    if completion_reason == RunCompletionReason.STOPPED_BY_USER.value:
        return BackgroundTaskStatus.STOPPED, None
    return BackgroundTaskStatus.FAILED, 1
