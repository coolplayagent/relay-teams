# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional, Protocol, cast, runtime_checkable
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.instances.enums import InstanceLifecycle, InstanceStatus
from relay_teams.agents.instances.models import (
    SubAgentInstance,
    create_subagent_instance,
)
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.ids import new_task_id
from relay_teams.agents.tasks.models import TaskEnvelope, TaskRecord, VerificationPlan
from relay_teams.logger import get_logger, log_event
from relay_teams.media import content_parts_from_text
from relay_teams.roles.role_models import RoleDefinition, RoleMode
from relay_teams.roles.runtime_role_resolver import RuntimeRoleResolver
from relay_teams.roles.temporary_role_models import (
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
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
from relay_teams.sessions.runs.event_stream import RunEventHub, publish_run_event_async
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
from relay_teams.workspace.ids import build_instance_conversation_id
from relay_teams.env.hook_runtime_env import merge_tool_hook_runtime_env
from relay_teams.hooks import (
    HookDecisionBundle,
    HookDecisionType,
    HookEventName,
    HookRuntimeSnapshot,
    HookService,
    SubagentStartInput,
    SubagentStopInput,
    TaskCreatedInput,
)

LOGGER = get_logger(__name__)
_COMPLETION_RETRY_INITIAL_DELAY_SECONDS = 1.0
_COMPLETION_RETRY_MAX_DELAY_SECONDS = 30.0
_SUBAGENT_COMMAND_PREFIX = "subagent:"
_TERMINAL_SUBAGENT_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.STOPPED,
        TaskStatus.TIMEOUT,
    }
)


class BackgroundTaskCompletionSink(Protocol):
    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        pass


@runtime_checkable
class AsyncBackgroundTaskCompletionSink(Protocol):
    async def handle_background_task_completion_async(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        pass


class _BackgroundTaskExecutor(Protocol):
    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> TaskExecutionResult:
        _ = (self, instance_id, role_id, task, user_prompt_override)
        return TaskExecutionResult(output="")


class _BackgroundTaskRunController(Protocol):
    def register_run_task(
        self,
        *,
        run_id: str,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        pass

    def unregister_run_task(self, run_id: str) -> None:
        pass

    def request_run_stop(self, run_id: str) -> bool:
        raise NotImplementedError

    def is_run_stop_requested(self, run_id: str) -> bool:
        raise NotImplementedError


class _BackgroundTaskRunRuntimeRepository(Protocol):
    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str | None = None,
        status: RunRuntimeStatus = RunRuntimeStatus.QUEUED,
        phase: RunRuntimePhase = RunRuntimePhase.IDLE,
    ) -> object:
        raise NotImplementedError

    def get(self, run_id: str) -> object | None:
        raise NotImplementedError

    def update(self, run_id: str, **changes: object) -> object:
        raise NotImplementedError


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
        lifecycle: Optional[InstanceLifecycle] = None,
        parent_instance_id: Optional[str] = None,
    ) -> None:
        pass

    def mark_status(self, instance_id: str, status: InstanceStatus) -> None:
        pass


class _BackgroundTaskTaskRepository(Protocol):
    def get(self, task_id: str) -> object:
        raise NotImplementedError

    def create(self, envelope: TaskEnvelope) -> object:
        raise NotImplementedError

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        pass


class _BackgroundTaskIntentRepository(Protocol):
    def get(
        self,
        run_id: str,
        *,
        fallback_session_id: str | None = None,
    ) -> IntentInput:
        raise NotImplementedError

    def upsert(self, *, run_id: str, session_id: str, intent: IntentInput) -> None:
        pass


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


class _ManagedSynchronousSubagentRuntime:
    def __init__(
        self,
        *,
        worker_task: asyncio.Task[None],
        parent_run_id: str,
        subagent_run_id: str,
        result_holder: dict[str, SynchronousSubagentResult],
    ) -> None:
        self.worker_task = worker_task
        self.parent_run_id = parent_run_id
        self.subagent_run_id = subagent_run_id
        self.result_holder = result_holder


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
    suppress_hooks: bool = False
    subagent_instance: SubAgentInstance
    subagent_task: TaskEnvelope


type SyncSubagentLaunchCallback = Callable[[BackgroundTaskRecord], Awaitable[None]]


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
        hook_service: HookService | None = None,
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
        self._hook_service = hook_service
        self._completion_sink: BackgroundTaskCompletionSink | None = None
        self._completion_retry_tasks: dict[str, asyncio.Task[None]] = {}
        self._subagent_runtimes: dict[str, _ManagedSubagentTaskRuntime] = {}
        self._synchronous_subagent_runtimes: dict[
            str, _ManagedSynchronousSubagentRuntime
        ] = {}
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
        tool_call_id: Optional[str],
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
        env = merge_tool_hook_runtime_env(env)
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
        tool_call_id: Optional[str],
        workspace_id: str,
        cwd: Path,
        subagent_role_id: str,
        subagent_role: RoleDefinition | None = None,
        title: str,
        prompt: str,
        on_launch_prepared: SyncSubagentLaunchCallback | None = None,
    ) -> BackgroundTaskRecord:
        task_execution_service = self._require_task_execution_service()
        run_control_manager = self._require_run_control_manager()
        background_task_id = f"background_task_{uuid4().hex[:12]}"
        prepared = await asyncio.to_thread(
            self._build_subagent_launch,
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            subagent_role_id=subagent_role_id,
            title=title,
            prompt=prompt,
        )
        record = await self._repository.upsert_async(
            BackgroundTaskRecord(
                background_task_id=background_task_id,
                run_id=run_id,
                session_id=session_id,
                kind=BackgroundTaskKind.SUBAGENT,
                instance_id=instance_id,
                role_id=role_id,
                tool_call_id=tool_call_id,
                title=prepared.normalized_title,
                input_text=prepared.normalized_prompt,
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
                subagent_suppress_hooks=prepared.suppress_hooks,
            ),
        )
        try:
            if on_launch_prepared is not None:
                await on_launch_prepared(record)
            await asyncio.to_thread(
                self._materialize_prepared_subagent_launch,
                parent_run_id=run_id,
                session_id=session_id,
                workspace_id=workspace_id,
                prepared=prepared,
                subagent_role=subagent_role,
            )
            hook_context = await self._execute_subagent_start_hooks(
                run_id=run_id,
                session_id=session_id,
                prepared=prepared,
            )
        except Exception as exc:
            record = await self._finalize_subagent_launch_failure(
                background_task_id=background_task_id,
                prepared=prepared,
                output=str(exc),
                synchronous=False,
            )
            await self._handle_background_task_completion(record)
            raise
        launch_prompt = _append_subagent_start_context(
            prompt=prepared.normalized_prompt,
            contexts=hook_context,
        )

        worker_start_gate = asyncio.Event()

        async def run_worker() -> None:
            try:
                await worker_start_gate.wait()
                result = await task_execution_service.execute(
                    instance_id=prepared.subagent_instance.instance_id,
                    role_id=prepared.subagent_role_id,
                    task=prepared.subagent_task,
                    user_prompt_override=launch_prompt,
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
            except Exception as worker_error:
                await self._finalize_subagent_record(
                    background_task_id=background_task_id,
                    status=BackgroundTaskStatus.FAILED,
                    exit_code=1,
                    output=str(worker_error),
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
        try:
            await self._publish_background_task_event_async(
                event_type=RunEventType.BACKGROUND_TASK_STARTED,
                record=record,
            )
        except Exception:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            current = await self._repository.get_async(background_task_id)
            if current is not None and current.is_active:
                await self._finalize_subagent_record(
                    background_task_id=background_task_id,
                    status=BackgroundTaskStatus.FAILED,
                    exit_code=1,
                    output="Task cancelled",
                )
            raise
        worker_start_gate.set()
        return record

    async def run_subagent(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        tool_call_id: str | None = None,
        parent_instance_id: str | None = None,
        parent_role_id: str | None = None,
        subagent_role_id: str,
        subagent_role: RoleDefinition | None = None,
        title: str,
        prompt: str,
        suppress_hooks: bool = False,
        on_launch_prepared: SyncSubagentLaunchCallback | None = None,
    ) -> SynchronousSubagentResult:
        prepared = await asyncio.to_thread(
            self._build_subagent_launch,
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            subagent_role_id=subagent_role_id,
            title=title,
            prompt=prompt,
            suppress_hooks=suppress_hooks,
        )
        if suppress_hooks:
            self._prime_subagent_hook_snapshot(subagent_run_id=prepared.subagent_run_id)
        background_task_id = f"sync_subagent_{uuid4().hex[:12]}"
        record = await self._repository.upsert_async(
            self._build_synchronous_subagent_record(
                background_task_id=background_task_id,
                run_id=run_id,
                session_id=session_id,
                workspace_id=workspace_id,
                prepared=prepared,
                tool_call_id=tool_call_id,
                parent_instance_id=parent_instance_id,
                parent_role_id=parent_role_id,
            ),
        )
        try:
            if on_launch_prepared is not None:
                await on_launch_prepared(record)
            await asyncio.to_thread(
                self._materialize_prepared_subagent_launch,
                parent_run_id=run_id,
                session_id=session_id,
                workspace_id=workspace_id,
                prepared=prepared,
                subagent_role=subagent_role,
            )
            hook_context = await self._execute_subagent_start_hooks(
                run_id=run_id,
                session_id=session_id,
                prepared=prepared,
            )
        except Exception as exc:
            await self._finalize_subagent_launch_failure(
                background_task_id=background_task_id,
                prepared=prepared,
                output=str(exc) or exc.__class__.__name__,
                synchronous=True,
            )
            raise
        launch_prompt = _append_subagent_start_context(
            prompt=prepared.normalized_prompt,
            contexts=hook_context,
        )
        return await self._run_prepared_synchronous_subagent(
            parent_run_id=run_id,
            session_id=session_id,
            background_task_id=background_task_id,
            prepared=prepared,
            launch_prompt=launch_prompt,
        )

    async def wait_for_subagent_run(
        self,
        *,
        parent_run_id: str,
        subagent_run_id: str,
    ) -> SynchronousSubagentResult:
        normalized_parent_run_id = parent_run_id.strip()
        normalized_run_id = subagent_run_id.strip()
        runtime = self._synchronous_subagent_runtimes.get(normalized_run_id)
        if runtime is not None:
            if runtime.parent_run_id != normalized_parent_run_id:
                raise KeyError(f"Unknown synchronous subagent run: {normalized_run_id}")
            await asyncio.shield(runtime.worker_task)
            result = runtime.result_holder.get("result")
            if result is None:
                raise RuntimeError("Subagent completed without returning a result")
            return result
        record = await self._synchronous_subagent_record(
            parent_run_id=normalized_parent_run_id,
            subagent_run_id=normalized_run_id,
        )
        if record is not None and record.is_active:
            finalized_record = (
                await self._finalize_active_synchronous_record_from_terminal_task(
                    record
                )
            )
            if finalized_record is not None:
                return await self._synchronous_subagent_result_from_record(
                    parent_run_id=normalized_parent_run_id,
                    subagent_run_id=normalized_run_id,
                )
            return await self._resume_synchronous_subagent_record(record)
        return await self._synchronous_subagent_result_from_record(
            parent_run_id=normalized_parent_run_id,
            subagent_run_id=normalized_run_id,
        )

    async def _resume_synchronous_subagent_record(
        self,
        record: BackgroundTaskRecord,
    ) -> SynchronousSubagentResult:
        prepared = self._prepared_launch_from_synchronous_record(record)
        if prepared.suppress_hooks:
            self._prime_subagent_hook_snapshot(subagent_run_id=prepared.subagent_run_id)
        try:
            await asyncio.to_thread(
                self._materialize_prepared_subagent_launch,
                parent_run_id=record.run_id,
                session_id=record.session_id,
                workspace_id=record.cwd,
                prepared=prepared,
                subagent_role=self._temporary_subagent_role_from_parent(
                    parent_run_id=record.run_id,
                    subagent_role_id=prepared.subagent_role_id,
                ),
            )
            hook_context = await self._execute_subagent_start_hooks(
                run_id=record.run_id,
                session_id=record.session_id,
                prepared=prepared,
            )
        except Exception as exc:
            await self._finalize_subagent_launch_failure(
                background_task_id=record.background_task_id,
                prepared=prepared,
                output=str(exc) or exc.__class__.__name__,
                synchronous=True,
            )
            raise
        launch_prompt = _append_subagent_start_context(
            prompt=prepared.normalized_prompt,
            contexts=hook_context,
        )
        return await self._run_prepared_synchronous_subagent(
            parent_run_id=record.run_id,
            session_id=record.session_id,
            background_task_id=record.background_task_id,
            prepared=prepared,
            launch_prompt=launch_prompt,
        )

    @staticmethod
    def _prepared_launch_from_synchronous_record(
        record: BackgroundTaskRecord,
    ) -> _PreparedSubagentLaunch:
        subagent_run_id = str(record.subagent_run_id or "").strip()
        subagent_role_id = str(record.subagent_role_id or "").strip()
        subagent_task_id = str(record.subagent_task_id or "").strip()
        subagent_instance_id = str(record.subagent_instance_id or "").strip()
        prompt = record.input_text.strip()
        if (
            not subagent_run_id
            or not subagent_role_id
            or not subagent_task_id
            or not subagent_instance_id
            or not prompt
        ):
            raise RuntimeError(
                "Synchronous subagent record is missing recovery metadata"
            )
        title = record.title.strip() or _default_subagent_title(
            role_id=subagent_role_id,
            prompt=prompt,
        )
        workspace_id = record.cwd.strip()
        if not workspace_id:
            raise RuntimeError("Synchronous subagent workspace is unavailable")
        subagent_instance = SubAgentInstance(
            instance_id=subagent_instance_id,
            role_id=subagent_role_id,
            workspace_id=workspace_id,
            conversation_id=build_instance_conversation_id(
                record.session_id,
                subagent_role_id,
                subagent_instance_id,
            ),
        )
        subagent_task = TaskEnvelope(
            task_id=subagent_task_id,
            session_id=record.session_id,
            parent_task_id=None,
            trace_id=subagent_run_id,
            role_id=subagent_role_id,
            title=title,
            objective=prompt,
            verification=VerificationPlan(checklist=("non_empty_response",)),
        )
        return _PreparedSubagentLaunch(
            normalized_prompt=prompt,
            normalized_title=title,
            subagent_run_id=subagent_run_id,
            subagent_role_id=subagent_role_id,
            suppress_hooks=record.subagent_suppress_hooks,
            subagent_instance=subagent_instance,
            subagent_task=subagent_task,
        )

    async def _finalize_active_synchronous_record_from_terminal_task(
        self,
        record: BackgroundTaskRecord,
    ) -> BackgroundTaskRecord | None:
        task_record = self._terminal_subagent_task_record(record)
        if task_record is None:
            return None
        status = _background_status_from_task_status(task_record.status)
        output = _output_from_terminal_task_record(task_record)
        finalized = await self._finalize_synchronous_subagent_record(
            background_task_id=record.background_task_id,
            status=status,
            output=output,
        )
        subagent_run_id = str(record.subagent_run_id or "").strip()
        if subagent_run_id:
            self._finalize_subagent_run_runtime(
                subagent_run_id=subagent_run_id,
                status=status,
                output=output,
            )
        return finalized

    def _terminal_subagent_task_record(
        self,
        record: BackgroundTaskRecord,
    ) -> TaskRecord | None:
        task_id = str(record.subagent_task_id or "").strip()
        if not task_id:
            return None
        try:
            task_record = self._require_task_repo().get(task_id)
        except (KeyError, RuntimeError, ValueError):
            return None
        if not isinstance(task_record, TaskRecord):
            return None
        if task_record.status not in _TERMINAL_SUBAGENT_TASK_STATUSES:
            return None
        return task_record

    async def _run_prepared_synchronous_subagent(
        self,
        *,
        parent_run_id: str,
        session_id: str,
        background_task_id: str,
        prepared: _PreparedSubagentLaunch,
        launch_prompt: str,
    ) -> SynchronousSubagentResult:
        task_execution_service = self._require_task_execution_service()
        run_control_manager = self._require_run_control_manager()
        result_holder: dict[str, SynchronousSubagentResult] = {}

        async def finalize_after_stop_hooks(
            *,
            status: BackgroundTaskStatus,
            output: str,
        ) -> None:
            hook_error: Exception | None = None
            final_status = status
            final_output = output
            try:
                await self._execute_subagent_stop_hooks_for_launch(
                    run_id=parent_run_id,
                    session_id=session_id,
                    prepared=prepared,
                    status=status,
                    output_text=output,
                )
            except Exception as stop_hook_error:
                hook_error = stop_hook_error
                if final_status == BackgroundTaskStatus.COMPLETED:
                    final_status = BackgroundTaskStatus.FAILED
                final_output = (
                    output.strip()
                    + "\n\n"
                    + f"Subagent stop hook failed: {stop_hook_error}"
                ).strip()
            await self._finalize_synchronous_subagent_record(
                background_task_id=background_task_id,
                status=final_status,
                output=final_output,
            )
            await asyncio.to_thread(
                self._finalize_subagent_run_runtime,
                subagent_run_id=prepared.subagent_run_id,
                status=final_status,
                output=final_output,
            )
            if hook_error is not None:
                raise hook_error

        async def run_worker() -> None:
            try:
                result = await task_execution_service.execute(
                    instance_id=prepared.subagent_instance.instance_id,
                    role_id=prepared.subagent_role_id,
                    task=prepared.subagent_task,
                    user_prompt_override=launch_prompt,
                )
            except asyncio.CancelledError:
                stopped = run_control_manager.is_run_stop_requested(
                    prepared.subagent_run_id
                )
                status = (
                    BackgroundTaskStatus.STOPPED
                    if stopped
                    else BackgroundTaskStatus.FAILED
                )
                output = "Task cancelled"
                await finalize_after_stop_hooks(
                    status=status,
                    output=output,
                )
                raise
            except Exception as worker_error:
                output = str(worker_error)
                await finalize_after_stop_hooks(
                    status=BackgroundTaskStatus.FAILED,
                    output=output,
                )
                raise RuntimeError(output) from worker_error

            status, _ = _status_from_execution_result(result)
            output = result.output.strip()
            await finalize_after_stop_hooks(
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
        managed_runtime = _ManagedSynchronousSubagentRuntime(
            worker_task=worker_task,
            parent_run_id=parent_run_id,
            subagent_run_id=prepared.subagent_run_id,
            result_holder=result_holder,
        )
        self._synchronous_subagent_runtimes[prepared.subagent_run_id] = managed_runtime
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
            current_runtime = self._synchronous_subagent_runtimes.get(
                prepared.subagent_run_id
            )
            if current_runtime is managed_runtime:
                self._synchronous_subagent_runtimes.pop(prepared.subagent_run_id, None)
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

    async def list_for_run_async(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        return tuple(
            record
            for record in await self._repository.list_by_run_async(run_id)
            if record.execution_mode == "background"
        )

    def subagent_record_for_tool_call(
        self,
        *,
        parent_run_id: str,
        tool_call_id: str,
    ) -> BackgroundTaskRecord | None:
        normalized_run_id = parent_run_id.strip()
        normalized_tool_call_id = tool_call_id.strip()
        if not normalized_run_id or not normalized_tool_call_id:
            return None
        for record in self._repository.list_by_run(normalized_run_id):
            if (
                record.kind == BackgroundTaskKind.SUBAGENT
                and record.tool_call_id == normalized_tool_call_id
            ):
                return record
        return None

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

    async def get_for_run_async(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = await self._repository.get_async(background_task_id)
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
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if not record.is_active:
            return await self._mark_completion_consumed_async(record), True
        if record.kind == BackgroundTaskKind.SUBAGENT:
            runtime = self._subagent_runtimes.get(background_task_id)
            if runtime is None:
                refreshed = await self.get_for_run_async(
                    run_id=run_id,
                    background_task_id=background_task_id,
                )
                if not refreshed.is_active:
                    return await self._mark_completion_consumed_async(refreshed), True
                return refreshed, False
            await asyncio.shield(runtime.worker_task)
            updated = await self.get_for_run_async(
                run_id=run_id,
                background_task_id=background_task_id,
            )
            return await self._mark_completion_consumed_async(updated), True
        updated, completed = await self._require_manager().wait_for_run(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        if not completed:
            return updated, False
        return await self._mark_completion_consumed_async(updated), True

    async def stop_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> BackgroundTaskRecord:
        record = await self.get_for_run_async(
            run_id=run_id,
            background_task_id=background_task_id,
        )
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
            updated = await self.get_for_run_async(
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
        current = await self._repository.get_async(background_task_id)
        if current is None:
            raise KeyError(f"Unknown background task: {background_task_id}")
        completed_at = datetime.now(tz=timezone.utc)
        summarized_output = output.strip()
        record = await self._repository.upsert_async(
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
        subagent_run_id = (
            runtime.subagent_run_id if runtime is not None else record.subagent_run_id
        )
        if subagent_run_id:
            await asyncio.to_thread(
                self._finalize_subagent_run_runtime,
                subagent_run_id=subagent_run_id,
                status=status,
                output=summarized_output,
            )
        if runtime is not None:
            self._require_run_control_manager().unregister_run_task(
                runtime.subagent_run_id
            )
        await self._publish_background_task_event_async(
            event_type=(
                RunEventType.BACKGROUND_TASK_STOPPED
                if status == BackgroundTaskStatus.STOPPED
                else RunEventType.BACKGROUND_TASK_COMPLETED
            ),
            record=record,
        )
        await self._execute_subagent_stop_hooks(
            record=record,
            output_text=summarized_output,
        )
        if status != BackgroundTaskStatus.STOPPED:
            await self._handle_background_task_completion(record)
        return record

    async def _finalize_subagent_launch_failure(
        self,
        *,
        background_task_id: str,
        prepared: _PreparedSubagentLaunch,
        output: str,
        synchronous: bool,
    ) -> BackgroundTaskRecord:
        await asyncio.to_thread(
            self._mark_subagent_launch_entities_failed,
            prepared=prepared,
            output=output,
        )
        if synchronous:
            record = await self._finalize_synchronous_subagent_record(
                background_task_id=background_task_id,
                status=BackgroundTaskStatus.FAILED,
                output=output,
            )
        else:
            current = await self._repository.get_async(background_task_id)
            if current is None:
                raise KeyError(f"Unknown background task: {background_task_id}")
            completed_at = datetime.now(tz=timezone.utc)
            summarized_output = output.strip()
            record = await self._repository.upsert_async(
                current.model_copy(
                    update={
                        "status": BackgroundTaskStatus.FAILED,
                        "exit_code": 1,
                        "recent_output": _recent_output_lines(summarized_output),
                        "output_excerpt": summarized_output,
                        "updated_at": completed_at,
                        "completed_at": completed_at,
                    }
                )
            )
            await self._publish_background_task_event_async(
                event_type=RunEventType.BACKGROUND_TASK_COMPLETED,
                record=record,
            )
        await asyncio.to_thread(
            self._finalize_subagent_run_runtime,
            subagent_run_id=prepared.subagent_run_id,
            status=BackgroundTaskStatus.FAILED,
            output=output,
        )
        return record

    def _mark_subagent_launch_entities_failed(
        self,
        *,
        prepared: _PreparedSubagentLaunch,
        output: str,
    ) -> None:
        summarized_output = output.strip()
        try:
            self._require_task_repo().update_status(
                prepared.subagent_task.task_id,
                TaskStatus.FAILED,
                assigned_instance_id=prepared.subagent_instance.instance_id,
                error_message=summarized_output,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="background_task.subagent_launch_task_mark_failed_skipped",
                message="Failed to mark subagent task failed during launch cleanup",
                payload={
                    "subagent_run_id": prepared.subagent_run_id,
                    "subagent_task_id": prepared.subagent_task.task_id,
                },
                exc_info=exc,
            )
        try:
            self._require_agent_repo().mark_status(
                prepared.subagent_instance.instance_id,
                InstanceStatus.FAILED,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                event="background_task.subagent_launch_instance_mark_failed_skipped",
                message="Failed to mark subagent instance failed during launch cleanup",
                payload={
                    "subagent_run_id": prepared.subagent_run_id,
                    "subagent_instance_id": prepared.subagent_instance.instance_id,
                },
                exc_info=exc,
            )

    @staticmethod
    def _build_synchronous_subagent_record(
        *,
        background_task_id: str,
        run_id: str,
        session_id: str,
        workspace_id: str,
        prepared: _PreparedSubagentLaunch,
        tool_call_id: str | None = None,
        parent_instance_id: str | None = None,
        parent_role_id: str | None = None,
    ) -> BackgroundTaskRecord:
        return BackgroundTaskRecord(
            background_task_id=background_task_id,
            run_id=run_id,
            session_id=session_id,
            kind=BackgroundTaskKind.SUBAGENT,
            instance_id=parent_instance_id or prepared.subagent_instance.instance_id,
            role_id=parent_role_id or prepared.subagent_role_id,
            tool_call_id=tool_call_id,
            title=prepared.normalized_title,
            input_text=prepared.normalized_prompt,
            command=f"{_SUBAGENT_COMMAND_PREFIX}{prepared.subagent_role_id}",
            cwd=workspace_id,
            execution_mode="foreground",
            status=BackgroundTaskStatus.RUNNING,
            tty=False,
            timeout_ms=None,
            log_path="",
            subagent_role_id=prepared.subagent_role_id,
            subagent_run_id=prepared.subagent_run_id,
            subagent_task_id=prepared.subagent_task.task_id,
            subagent_instance_id=prepared.subagent_instance.instance_id,
            subagent_suppress_hooks=prepared.suppress_hooks,
        )

    async def _finalize_synchronous_subagent_record(
        self,
        *,
        background_task_id: str,
        status: BackgroundTaskStatus,
        output: str,
    ) -> BackgroundTaskRecord:
        current = await self._repository.get_async(background_task_id)
        if current is None:
            raise KeyError(f"Unknown background task: {background_task_id}")
        completed_at = datetime.now(tz=timezone.utc)
        summarized_output = output.strip()
        return await self._repository.upsert_async(
            current.model_copy(
                update={
                    "status": status,
                    "exit_code": 0 if status == BackgroundTaskStatus.COMPLETED else 1,
                    "recent_output": _recent_output_lines(summarized_output),
                    "output_excerpt": summarized_output,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "completion_notified_at": completed_at,
                }
            )
        )

    async def _synchronous_subagent_result_from_record(
        self,
        *,
        parent_run_id: str,
        subagent_run_id: str,
    ) -> SynchronousSubagentResult:
        record = await self._synchronous_subagent_record(
            parent_run_id=parent_run_id,
            subagent_run_id=subagent_run_id,
        )
        if record is None or record.is_active:
            raise KeyError(f"Unknown synchronous subagent run: {subagent_run_id}")
        if record.status != BackgroundTaskStatus.COMPLETED:
            raise RuntimeError(record.output_excerpt or "Subagent failed")
        return SynchronousSubagentResult(
            run_id=record.subagent_run_id or subagent_run_id,
            instance_id=record.subagent_instance_id or "",
            role_id=record.subagent_role_id or "",
            task_id=record.subagent_task_id or "",
            title=record.title,
            output=record.output_excerpt,
        )

    async def _synchronous_subagent_record(
        self,
        *,
        parent_run_id: str,
        subagent_run_id: str,
    ) -> BackgroundTaskRecord | None:
        for record in await self._repository.list_all_async():
            if (
                record.kind == BackgroundTaskKind.SUBAGENT
                and record.execution_mode == "foreground"
                and record.run_id == parent_run_id
                and record.subagent_run_id == subagent_run_id
            ):
                return record
        return None

    async def _handle_background_task_completion(
        self, record: BackgroundTaskRecord
    ) -> None:
        await asyncio.sleep(0)
        delivered = await self._attempt_completion_delivery_async(
            record.background_task_id
        )
        if not delivered and self._completion_sink is not None:
            self._schedule_completion_retry(record.background_task_id)

    async def _attempt_completion_delivery_async(self, background_task_id: str) -> bool:
        current = await self._repository.get_async(background_task_id)
        if current is None:
            return True
        if not self._should_notify_completion(current):
            return True
        if self._completion_sink is None:
            return False
        message = build_background_task_completion_message(current)
        try:
            if isinstance(self._completion_sink, AsyncBackgroundTaskCompletionSink):
                await self._completion_sink.handle_background_task_completion_async(
                    record=current,
                    message=message,
                )
            else:
                await asyncio.to_thread(
                    self._completion_sink.handle_background_task_completion,
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
        _ = await self._mark_completion_consumed_async(current)
        return True

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
                if await self._attempt_completion_delivery_async(background_task_id):
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

    async def _mark_completion_consumed_async(
        self, record: BackgroundTaskRecord
    ) -> BackgroundTaskRecord:
        if not self._should_notify_completion(record):
            return record
        completed_at = datetime.now(tz=timezone.utc)
        return await self._repository.upsert_async(
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

    async def _publish_background_task_event_async(
        self,
        *,
        event_type: RunEventType,
        record: BackgroundTaskRecord,
    ) -> None:
        if self._run_event_hub is None:
            return
        await publish_run_event_async(
            self._run_event_hub,
            RunEvent(
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                task_id=None,
                instance_id=record.instance_id,
                role_id=record.role_id,
                event_type=event_type,
                payload_json=record.model_dump_json(),
            ),
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
        subagent_role: RoleDefinition | None = None,
        title: str,
        prompt: str,
        suppress_hooks: bool = False,
    ) -> _PreparedSubagentLaunch:
        prepared = self._build_subagent_launch(
            run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            subagent_role_id=subagent_role_id,
            title=title,
            prompt=prompt,
            suppress_hooks=suppress_hooks,
        )
        self._materialize_prepared_subagent_launch(
            parent_run_id=run_id,
            session_id=session_id,
            workspace_id=workspace_id,
            prepared=prepared,
            subagent_role=subagent_role,
        )
        return prepared

    def _build_subagent_launch(
        self,
        *,
        run_id: str,
        session_id: str,
        workspace_id: str,
        subagent_role_id: str,
        title: str,
        prompt: str,
        suppress_hooks: bool = False,
    ) -> _PreparedSubagentLaunch:
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
        return _PreparedSubagentLaunch(
            normalized_prompt=normalized_prompt,
            normalized_title=normalized_title,
            subagent_run_id=subagent_run_id,
            subagent_role_id=subagent_role_id,
            suppress_hooks=suppress_hooks,
            subagent_instance=subagent_instance,
            subagent_task=subagent_task,
        )

    def _materialize_prepared_subagent_launch(
        self,
        *,
        parent_run_id: str,
        session_id: str,
        workspace_id: str,
        prepared: _PreparedSubagentLaunch,
        subagent_role: RoleDefinition | None = None,
    ) -> None:
        agent_repo = self._require_agent_repo()
        task_repo = self._require_task_repo()
        subagent_instance = prepared.subagent_instance
        subagent_task = prepared.subagent_task
        agent_repo.upsert_instance(
            run_id=prepared.subagent_run_id,
            trace_id=prepared.subagent_run_id,
            session_id=session_id,
            instance_id=subagent_instance.instance_id,
            role_id=prepared.subagent_role_id,
            workspace_id=workspace_id,
            conversation_id=subagent_instance.conversation_id,
            status=InstanceStatus.IDLE,
            lifecycle=InstanceLifecycle.EPHEMERAL,
        )
        if not self._subagent_task_exists(task_repo, subagent_task.task_id):
            task_repo.create(subagent_task)
            self._record_subagent_task_created(
                task=subagent_task,
                suppress_hooks=prepared.suppress_hooks,
            )
        task_repo.update_status(
            subagent_task.task_id,
            TaskStatus.ASSIGNED,
            assigned_instance_id=subagent_instance.instance_id,
        )
        self._upsert_subagent_intent(
            parent_run_id=parent_run_id,
            subagent_run_id=prepared.subagent_run_id,
            session_id=session_id,
            subagent_role_id=prepared.subagent_role_id,
            prompt=prepared.normalized_prompt,
        )
        self._clone_subagent_role_snapshot(
            subagent_run_id=prepared.subagent_run_id,
            session_id=session_id,
            subagent_role=subagent_role,
        )
        if self._run_runtime_repo is not None:
            _ = self._run_runtime_repo.ensure(
                run_id=prepared.subagent_run_id,
                session_id=session_id,
                root_task_id=subagent_task.task_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.SUBAGENT_RUNNING,
            )
            _ = self._run_runtime_repo.update(
                prepared.subagent_run_id,
                status=RunRuntimeStatus.RUNNING,
                phase=RunRuntimePhase.SUBAGENT_RUNNING,
                active_instance_id=subagent_instance.instance_id,
                active_task_id=subagent_task.task_id,
                active_role_id=prepared.subagent_role_id,
                active_subagent_instance_id=subagent_instance.instance_id,
                last_error=None,
            )

    @staticmethod
    def _subagent_task_exists(
        task_repo: _BackgroundTaskTaskRepository,
        task_id: str,
    ) -> bool:
        try:
            _ = task_repo.get(task_id)
        except KeyError:
            return False
        return True

    def _clone_subagent_role_snapshot(
        self,
        *,
        subagent_run_id: str,
        session_id: str,
        subagent_role: RoleDefinition | None,
    ) -> None:
        if subagent_role is None:
            return
        runtime_role_resolver = self._get_runtime_role_resolver()
        if runtime_role_resolver is None:
            return
        runtime_role_resolver.create_temporary_role(
            run_id=subagent_run_id,
            session_id=session_id,
            source=TemporaryRoleSource.SKILL_TEAM,
            role=_temporary_role_spec_from_definition(subagent_role),
        )

    def _temporary_subagent_role_from_parent(
        self,
        *,
        parent_run_id: str,
        subagent_role_id: str,
    ) -> RoleDefinition | None:
        runtime_role_resolver = self._get_runtime_role_resolver()
        if runtime_role_resolver is None:
            return None
        try:
            return runtime_role_resolver.get_temporary_role(
                run_id=parent_run_id,
                role_id=subagent_role_id,
            )
        except KeyError:
            return None

    def _get_runtime_role_resolver(self) -> RuntimeRoleResolver | None:
        if self._task_execution_service is None:
            return None
        return cast(
            RuntimeRoleResolver | None,
            getattr(self._task_execution_service, "runtime_role_resolver", None),
        )

    def _prime_subagent_hook_snapshot(self, *, subagent_run_id: str) -> None:
        if self._hook_service is None:
            return
        self._hook_service.set_run_snapshot(subagent_run_id, HookRuntimeSnapshot())

    def _record_subagent_task_created(
        self,
        *,
        task: TaskEnvelope,
        suppress_hooks: bool,
    ) -> None:
        if self._hook_service is None or suppress_hooks or task.parent_task_id is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._hook_service.execute(
                event_input=TaskCreatedInput(
                    event_name=HookEventName.TASK_CREATED,
                    session_id=task.session_id,
                    run_id=task.trace_id,
                    trace_id=task.trace_id,
                    task_id=task.task_id,
                    role_id=task.role_id,
                    created_task_id=task.task_id,
                    parent_task_id=task.parent_task_id,
                    title=task.title or "",
                    objective=task.objective,
                ),
                run_event_hub=self._run_event_hub,
            )
        )

    async def _execute_subagent_start_hooks(
        self,
        *,
        run_id: str,
        session_id: str,
        prepared: _PreparedSubagentLaunch,
    ) -> tuple[str, ...]:
        if self._hook_service is None or prepared.suppress_hooks:
            return ()
        bundle = await self._hook_service.execute(
            event_input=SubagentStartInput(
                event_name=HookEventName.SUBAGENT_START,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                role_id=prepared.subagent_role_id,
                parent_run_id=run_id,
                subagent_run_id=prepared.subagent_run_id,
                subagent_task_id=prepared.subagent_task.task_id,
                subagent_instance_id=prepared.subagent_instance.instance_id,
                subagent_role_id=prepared.subagent_role_id,
                title=prepared.normalized_title,
                prompt=prepared.normalized_prompt,
            ),
            run_event_hub=self._run_event_hub,
        )
        return bundle.additional_context

    async def _execute_subagent_stop_hooks(
        self,
        *,
        record: BackgroundTaskRecord,
        output_text: str,
    ) -> None:
        if self._hook_service is None:
            return
        bundle = await self._hook_service.execute(
            event_input=SubagentStopInput(
                event_name=HookEventName.SUBAGENT_STOP,
                session_id=record.session_id,
                run_id=record.run_id,
                trace_id=record.run_id,
                role_id=record.subagent_role_id,
                parent_run_id=record.run_id,
                subagent_run_id=record.subagent_run_id or "",
                subagent_task_id=record.subagent_task_id or "",
                subagent_instance_id=record.subagent_instance_id or "",
                subagent_role_id=record.subagent_role_id or "",
                title=record.title,
                status=record.status.value,
                output_text=output_text,
            ),
            run_event_hub=self._run_event_hub,
        )
        _raise_for_subagent_stop_decision(bundle)

    async def _execute_subagent_stop_hooks_for_launch(
        self,
        *,
        run_id: str,
        session_id: str,
        prepared: _PreparedSubagentLaunch,
        status: BackgroundTaskStatus,
        output_text: str,
    ) -> None:
        if self._hook_service is None or prepared.suppress_hooks:
            return
        bundle = await self._hook_service.execute(
            event_input=SubagentStopInput(
                event_name=HookEventName.SUBAGENT_STOP,
                session_id=session_id,
                run_id=run_id,
                trace_id=run_id,
                role_id=prepared.subagent_role_id,
                parent_run_id=run_id,
                subagent_run_id=prepared.subagent_run_id,
                subagent_task_id=prepared.subagent_task.task_id,
                subagent_instance_id=prepared.subagent_instance.instance_id,
                subagent_role_id=prepared.subagent_role_id,
                title=prepared.normalized_title,
                status=status.value,
                output_text=output_text,
            ),
            run_event_hub=self._run_event_hub,
        )
        _raise_for_subagent_stop_decision(bundle)

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
        if self._hook_service is not None:
            self._hook_service.clear_run(subagent_run_id)
        runtime_role_resolver = self._get_runtime_role_resolver()
        if runtime_role_resolver is not None:
            runtime_role_resolver.cleanup_run(run_id=subagent_run_id)
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


def _temporary_role_spec_from_definition(role: RoleDefinition) -> TemporaryRoleSpec:
    return TemporaryRoleSpec(
        role_id=role.role_id,
        name=role.name,
        description=role.description,
        version=role.version,
        tools=role.tools,
        mcp_servers=role.mcp_servers,
        skills=role.skills,
        model_profile=role.model_profile,
        bound_agent_id=role.bound_agent_id,
        execution_surface=role.execution_surface,
        mode=RoleMode.SUBAGENT,
        memory_profile=role.memory_profile,
        system_prompt=role.system_prompt,
    )


def _default_subagent_title(*, role_id: str, prompt: str) -> str:
    summary = " ".join(prompt.split())
    if not summary:
        return role_id
    return f"{role_id}: {summary[:80]}"


def _append_subagent_start_context(
    *,
    prompt: str,
    contexts: tuple[str, ...],
) -> str:
    context_text = "\n\n".join(item.strip() for item in contexts if item.strip())
    if not context_text:
        return prompt
    if not prompt.strip():
        return context_text
    return (
        prompt.rstrip()
        + "\n\n"
        + "Additional context from SubagentStart hooks:\n"
        + context_text
    )


def _raise_for_subagent_stop_decision(bundle: HookDecisionBundle) -> None:
    if bundle.decision not in {HookDecisionType.DENY, HookDecisionType.RETRY}:
        return
    reason_parts = [bundle.reason, *bundle.additional_context]
    reason = "\n\n".join(part.strip() for part in reason_parts if part.strip())
    raise RuntimeError(reason or "Subagent stop rejected by runtime hooks.")


def _recent_output_lines(output: str) -> tuple[str, ...]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ()
    return tuple(lines[-3:])


def _background_status_from_task_status(status: TaskStatus) -> BackgroundTaskStatus:
    if status == TaskStatus.COMPLETED:
        return BackgroundTaskStatus.COMPLETED
    if status == TaskStatus.STOPPED:
        return BackgroundTaskStatus.STOPPED
    return BackgroundTaskStatus.FAILED


def _output_from_terminal_task_record(record: TaskRecord) -> str:
    if record.status == TaskStatus.COMPLETED:
        return record.result or ""
    return record.error_message or record.result or record.status.value


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
