# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, cast

import pytest

from relay_teams.agents.tasks.models import TaskEnvelope
from relay_teams.agents.orchestration.task_execution_service import TaskExecutionResult
from relay_teams.sessions.runs.background_tasks.command_runtime import (
    normalize_timeout,
)
from relay_teams.sessions.runs.background_tasks.service import (
    BackgroundTaskService,
    SynchronousSubagentResult,
)
from relay_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.sessions.runs.background_tasks.projection import (
    build_background_task_result_payload,
)
from relay_teams.sessions.runs.assistant_errors import RunCompletionReason
from relay_teams.sessions.runs.enums import ExecutionMode
from relay_teams.sessions.runs.run_models import IntentInput, RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)
from relay_teams.hooks.hook_models import HookRuntimeSnapshot
from relay_teams.hooks import HookService
from relay_teams.workspace import WorkspaceHandle


class _FakeBackgroundTaskManager:
    def __init__(self) -> None:
        self._listener: Callable[[BackgroundTaskRecord], Awaitable[None]] | None = None
        self.records: tuple[BackgroundTaskRecord, ...] = ()
        self.start_calls: list[dict[str, object]] = []
        self.interact_result: tuple[BackgroundTaskRecord, bool] | None = None
        self.wait_result: tuple[BackgroundTaskRecord, bool] | None = None

    def set_completion_listener(
        self,
        listener: Callable[[BackgroundTaskRecord], Awaitable[None]] | None,
    ) -> None:
        self._listener = listener

    def list_for_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        return tuple(record for record in self.records if record.run_id == run_id)

    def get_for_run(
        self, *, run_id: str, background_task_id: str
    ) -> BackgroundTaskRecord:
        for record in self.records:
            if (
                record.run_id == run_id
                and record.background_task_id == background_task_id
            ):
                return record
        raise KeyError(background_task_id)

    async def start_session(
        self,
        *,
        run_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        tool_call_id: str | None,
        workspace: object,
        command: str,
        cwd: Path,
        timeout_ms: int | None,
        env: dict[str, str] | None,
        tty: bool,
        execution_mode: Literal["foreground", "background"] = "background",
    ) -> BackgroundTaskRecord:
        _ = (workspace, env)
        self.start_calls.append(
            {
                "run_id": run_id,
                "session_id": session_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "tool_call_id": tool_call_id,
                "command": command,
                "cwd": cwd,
                "timeout_ms": timeout_ms,
                "tty": tty,
                "execution_mode": execution_mode,
            }
        )
        record = _build_record(
            background_task_id="exec-started",
            execution_mode=execution_mode,
            status=(
                BackgroundTaskStatus.RUNNING
                if execution_mode == "background"
                else BackgroundTaskStatus.COMPLETED
            ),
        ).model_copy(
            update={
                "run_id": run_id,
                "session_id": session_id,
                "instance_id": instance_id,
                "role_id": role_id,
                "tool_call_id": tool_call_id,
                "command": command,
                "cwd": str(cwd),
                "timeout_ms": timeout_ms,
                "tty": tty,
            }
        )
        self.records = self.records + (record,)
        return record

    async def interact_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        chars: str,
        yield_time_ms: int | None,
        is_initial_poll: bool,
    ) -> tuple[BackgroundTaskRecord, bool]:
        _ = (run_id, background_task_id, chars, yield_time_ms, is_initial_poll)
        if self.interact_result is None:
            raise AssertionError("interact_result not configured")
        return self.interact_result

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
    ) -> tuple[BackgroundTaskRecord, bool]:
        _ = (run_id, background_task_id)
        if self.wait_result is None:
            raise AssertionError("wait_result not configured")
        return self.wait_result


class _CapturingCompletionSink:
    def __init__(self) -> None:
        self.calls: list[tuple[BackgroundTaskRecord, str]] = []

    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        self.calls.append((record, message))


class _FailingThenCapturingCompletionSink:
    def __init__(self, *, failures_before_success: int) -> None:
        self._failures_before_success = failures_before_success
        self.attempts = 0
        self.calls: list[tuple[BackgroundTaskRecord, str]] = []

    def handle_background_task_completion(
        self,
        *,
        record: BackgroundTaskRecord,
        message: str,
    ) -> None:
        self.attempts += 1
        if self.attempts <= self._failures_before_success:
            raise RuntimeError("transient sink failure")
        self.calls.append((record, message))


class _FakeTaskExecutionService:
    def __init__(
        self,
        *,
        result: TaskExecutionResult,
        gate: asyncio.Event | None = None,
    ) -> None:
        self._result = result
        self._gate = gate
        self.calls: list[dict[str, object]] = []

    async def execute(
        self,
        *,
        instance_id: str,
        role_id: str,
        task: TaskEnvelope,
        user_prompt_override: str | None = None,
    ) -> TaskExecutionResult:
        self.calls.append(
            {
                "instance_id": instance_id,
                "role_id": role_id,
                "task": task,
                "user_prompt_override": user_prompt_override,
            }
        )
        if self._gate is not None:
            await self._gate.wait()
        return self._result


class _FakeAgentRepo:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def upsert_instance(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


class _FakeTaskRepo:
    def __init__(self) -> None:
        self.created: list[object] = []
        self.status_updates: list[dict[str, object]] = []

    def create(self, envelope: object) -> object:
        self.created.append(envelope)
        return envelope

    def update_status(
        self,
        task_id: str,
        status: object,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self.status_updates.append(
            {
                "task_id": task_id,
                "status": status,
                "assigned_instance_id": assigned_instance_id,
                "result": result,
                "error_message": error_message,
            }
        )


class _FakeRunIntentRepo:
    def __init__(self, parent_intent: IntentInput) -> None:
        self._records: dict[str, IntentInput] = {"run-1": parent_intent}

    def get(
        self,
        run_id: str,
        *,
        fallback_session_id: str | None = None,
    ) -> IntentInput:
        _ = fallback_session_id
        if run_id not in self._records:
            raise KeyError(run_id)
        return self._records[run_id]

    def upsert(self, *, run_id: str, session_id: str, intent: IntentInput) -> None:
        _ = session_id
        self._records[run_id] = intent


class _FakeRunControlManager:
    def __init__(self) -> None:
        self.registered_run_ids: list[str] = []
        self.unregistered_run_ids: list[str] = []
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}
        self._stopped_run_ids: set[str] = set()

    def register_run_task(
        self,
        *,
        run_id: str,
        session_id: str,
        task: asyncio.Task[None],
    ) -> None:
        _ = session_id
        self.registered_run_ids.append(run_id)
        self._worker_tasks[run_id] = task

    def unregister_run_task(self, run_id: str) -> None:
        self.unregistered_run_ids.append(run_id)
        self._worker_tasks.pop(run_id, None)

    def request_run_stop(self, run_id: str) -> bool:
        self._stopped_run_ids.add(run_id)
        task = self._worker_tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        return task is not None

    def is_run_stop_requested(self, run_id: str) -> bool:
        return run_id in self._stopped_run_ids


class _CapturingHookService:
    def __init__(self) -> None:
        self.executed_events: list[str] = []
        self.snapshots: list[str] = []
        self.cleared: list[str] = []

    async def execute(
        self,
        *,
        event_input: object,
        run_event_hub: object | None,
    ) -> object:
        _ = run_event_hub
        self.executed_events.append(str(getattr(event_input, "event_name").value))
        return object()

    def set_run_snapshot(self, run_id: str, snapshot: HookRuntimeSnapshot) -> None:
        _ = snapshot
        self.snapshots.append(run_id)

    def clear_run(self, run_id: str) -> None:
        self.cleared.append(run_id)


def _build_record(
    *,
    background_task_id: str = "exec-1",
    execution_mode: Literal["foreground", "background"] = "background",
    status: BackgroundTaskStatus = BackgroundTaskStatus.COMPLETED,
    recent_output: tuple[str, ...] = (),
    output_excerpt: str = "",
) -> BackgroundTaskRecord:
    return BackgroundTaskRecord(
        background_task_id=background_task_id,
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        command="python worker.py",
        cwd="C:/workspace",
        execution_mode=execution_mode,
        status=status,
        exit_code=0,
        recent_output=recent_output,
        output_excerpt=output_excerpt,
        log_path="tmp/background_tasks/exec-1.log",
    )


def _parent_intent() -> IntentInput:
    return IntentInput(
        session_id="session-1",
        execution_mode=ExecutionMode.AI,
        thinking=RunThinkingConfig(enabled=True, effort="medium"),
        session_mode=SessionMode.NORMAL,
    )


@pytest.mark.asyncio
async def test_background_task_service_notifies_sink_and_persists_completion_marker(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    sink = _CapturingCompletionSink()
    service.bind_completion_sink(sink)
    record = repo.upsert(
        _build_record(recent_output=("done & <ok>",), output_excerpt="ignored")
    )

    assert manager._listener is not None
    await manager._listener(record)

    persisted = repo.get(record.background_task_id)
    assert persisted is not None
    assert persisted.completion_notified_at is not None
    assert len(sink.calls) == 1
    _, message = sink.calls[0]
    assert message.startswith(
        "A managed background task finished. The notification below includes the same result payload returned by wait_background_task"
    )
    assert "<background-task-id>exec-1</background-task-id>" in message
    assert "<status>completed</status>" in message
    assert "done &amp; &lt;ok&gt;" in message
    payload_match = re.search(
        r"<result-payload>\n(.*?)\n</result-payload>",
        message,
        re.DOTALL,
    )
    assert payload_match is not None
    assert json.loads(html.unescape(payload_match.group(1))) == (
        build_background_task_result_payload(
            record,
            completed=True,
            include_task_id=True,
        )
    )


@pytest.mark.asyncio
async def test_background_task_service_skips_non_background_notifications(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-foreground.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    sink = _CapturingCompletionSink()
    service.bind_completion_sink(sink)
    record = repo.upsert(_build_record(execution_mode="foreground"))

    assert manager._listener is not None
    await manager._listener(record)

    persisted = repo.get(record.background_task_id)
    assert persisted is not None
    assert persisted.completion_notified_at is None
    assert sink.calls == []


def test_background_task_service_lists_only_background_records(tmp_path: Path) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-list.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    foreground = _build_record(
        background_task_id="exec-foreground",
        execution_mode="foreground",
    )
    background = _build_record(background_task_id="exec-background")
    repo.upsert(foreground)
    repo.upsert(background)

    records = service.list_for_run("run-1")

    assert tuple(record.background_task_id for record in records) == (
        "exec-background",
    )


@pytest.mark.asyncio
async def test_execute_command_preserves_background_timeout_default_when_omitted(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-execute-bg.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    manager.interact_result = (
        _build_record(
            background_task_id="exec-started",
            status=BackgroundTaskStatus.RUNNING,
        ),
        False,
    )

    updated, completed = await service.execute_command(
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        workspace=cast(WorkspaceHandle, object()),
        command="python worker.py",
        cwd=Path("C:/workspace"),
        yield_time_ms=1_000,
        timeout_ms=None,
        env=None,
        tty=False,
        background=True,
    )

    assert manager.start_calls[0]["timeout_ms"] is None
    assert completed is False
    assert updated.background_task_id == "exec-started"


@pytest.mark.asyncio
async def test_execute_command_keeps_foreground_timeout_normalization_when_omitted(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-execute-fg.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    manager.wait_result = (
        _build_record(
            background_task_id="exec-started",
            execution_mode="foreground",
        ),
        True,
    )

    updated, completed = await service.execute_command(
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        workspace=cast(WorkspaceHandle, object()),
        command="python worker.py",
        cwd=Path("C:/workspace"),
        yield_time_ms=1_000,
        timeout_ms=None,
        env=None,
        tty=False,
        background=False,
    )

    assert manager.start_calls[0]["timeout_ms"] == normalize_timeout(None)
    assert completed is True
    assert updated.background_task_id == "exec-started"


def test_background_task_service_get_for_run_rejects_foreground_records(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-get.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    manager.records = (_build_record(execution_mode="foreground"),)

    try:
        service.get_for_run(run_id="run-1", background_task_id="exec-1")
    except KeyError as exc:
        assert "Unknown background task" in str(exc)
    else:
        raise AssertionError("Expected foreground background task lookup to fail")


@pytest.mark.asyncio
async def test_wait_for_run_marks_completed_background_task_as_consumed(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-wait.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    completed = repo.upsert(_build_record())
    manager.records = (completed,)

    updated, done = await service.wait_for_run(
        run_id="run-1",
        background_task_id="exec-1",
    )

    persisted = repo.get("exec-1")
    assert done is True
    assert updated.completion_notified_at is not None
    assert persisted is not None
    assert persisted.completion_notified_at is not None


@pytest.mark.asyncio
async def test_background_task_service_skips_notification_when_wait_already_consumed_completion(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-consumed.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    sink = _CapturingCompletionSink()
    service.bind_completion_sink(sink)
    fresh = _build_record()
    repo.upsert(fresh)
    consumed = repo.upsert(
        fresh.model_copy(
            update={"completion_notified_at": fresh.updated_at},
        )
    )

    assert manager._listener is not None
    await manager._listener(fresh)

    persisted = repo.get(consumed.background_task_id)
    assert persisted is not None
    assert persisted.completion_notified_at == consumed.completion_notified_at
    assert sink.calls == []


@pytest.mark.asyncio
async def test_background_task_service_retries_completion_delivery_after_sink_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from relay_teams.sessions.runs.background_tasks import service as service_module

    async def _fake_sleep(delay: float) -> None:
        _ = delay

    monkeypatch.setattr(service_module.asyncio, "sleep", _fake_sleep)

    repo = BackgroundTaskRepository(tmp_path / "background-task-service-retry.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    sink = _FailingThenCapturingCompletionSink(failures_before_success=1)
    service.bind_completion_sink(sink)
    record = repo.upsert(_build_record())

    assert manager._listener is not None
    await manager._listener(record)
    retry_task = service._completion_retry_tasks.get(record.background_task_id)
    assert retry_task is not None
    await retry_task

    persisted = repo.get(record.background_task_id)
    assert persisted is not None
    assert sink.attempts == 2
    assert len(sink.calls) == 1
    assert persisted.completion_notified_at is not None
    assert service._completion_retry_tasks == {}


@pytest.mark.asyncio
async def test_background_task_service_flushes_pending_completion_when_sink_binds(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-bind.db")
    manager = _FakeBackgroundTaskManager()
    service = BackgroundTaskService(
        background_task_manager=cast(BackgroundTaskManager, manager),
        repository=repo,
    )
    record = repo.upsert(_build_record())

    assert manager._listener is not None
    await manager._listener(record)
    persisted = repo.get(record.background_task_id)
    assert persisted is not None
    assert persisted.completion_notified_at is None

    sink = _CapturingCompletionSink()
    service.bind_completion_sink(sink)

    refreshed = repo.get(record.background_task_id)
    assert refreshed is not None
    assert refreshed.completion_notified_at is not None
    assert len(sink.calls) == 1


def test_background_task_service_bind_completion_sink_flushes_pending_without_running_loop(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-startup.db")
    service = BackgroundTaskService(
        background_task_manager=None,
        repository=repo,
    )
    record = repo.upsert(_build_record())
    sink = _CapturingCompletionSink()

    service.bind_completion_sink(sink)

    persisted = repo.get(record.background_task_id)
    assert persisted is not None
    assert persisted.completion_notified_at is not None
    assert len(sink.calls) == 1


@pytest.mark.asyncio
async def test_background_task_service_start_subagent_completes_and_persists_result(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(tmp_path / "background-task-service-subagent.db")
    runtime_repo = RunRuntimeRepository(
        tmp_path / "background-task-service-subagent.db"
    )
    executor = _FakeTaskExecutionService(
        result=TaskExecutionResult(
            output="analysis complete",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )
    )
    agent_repo = _FakeAgentRepo()
    task_repo = _FakeTaskRepo()
    intent_repo = _FakeRunIntentRepo(_parent_intent())
    run_control_manager = _FakeRunControlManager()
    service = BackgroundTaskService(
        background_task_manager=None,
        repository=repo,
        task_execution_service=executor,
        agent_repo=agent_repo,
        task_repo=task_repo,
        run_intent_repo=intent_repo,
        run_control_manager=run_control_manager,
        run_runtime_repo=runtime_repo,
    )

    started = await service.start_subagent(
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="MainAgent",
        tool_call_id="call-1",
        workspace_id="workspace-1",
        cwd=Path("C:/workspace"),
        subagent_role_id="Crafter",
        title="Investigate failures",
        prompt="Inspect the failing tests and summarize the cause.",
    )
    updated, completed = await service.wait_for_run(
        run_id="run-1",
        background_task_id=started.background_task_id,
    )

    persisted = repo.get(started.background_task_id)
    assert completed is True
    assert updated.kind == BackgroundTaskKind.SUBAGENT
    assert updated.status == BackgroundTaskStatus.COMPLETED
    assert updated.subagent_role_id == "Crafter"
    assert updated.subagent_run_id is not None
    assert updated.subagent_instance_id is not None
    assert updated.output_excerpt == "analysis complete"
    assert persisted is not None
    assert persisted.status == BackgroundTaskStatus.COMPLETED
    assert persisted.output_excerpt == "analysis complete"
    assert agent_repo.calls[0]["run_id"] == updated.subagent_run_id
    assert executor.calls[0]["role_id"] == "Crafter"
    assert updated.subagent_run_id in intent_repo._records
    assert run_control_manager.unregistered_run_ids == [updated.subagent_run_id]
    runtime = runtime_repo.get(updated.subagent_run_id or "")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL


@pytest.mark.asyncio
async def test_background_task_service_run_subagent_returns_synchronous_result(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(
        tmp_path / "background-task-service-subagent-sync.db"
    )
    runtime_repo = RunRuntimeRepository(
        tmp_path / "background-task-service-subagent-sync.db"
    )
    executor = _FakeTaskExecutionService(
        result=TaskExecutionResult(
            output="analysis complete",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )
    )
    agent_repo = _FakeAgentRepo()
    task_repo = _FakeTaskRepo()
    intent_repo = _FakeRunIntentRepo(_parent_intent())
    run_control_manager = _FakeRunControlManager()
    service = BackgroundTaskService(
        background_task_manager=None,
        repository=repo,
        task_execution_service=executor,
        agent_repo=agent_repo,
        task_repo=task_repo,
        run_intent_repo=intent_repo,
        run_control_manager=run_control_manager,
        run_runtime_repo=runtime_repo,
    )

    result = await service.run_subagent(
        run_id="run-1",
        session_id="session-1",
        workspace_id="workspace-1",
        subagent_role_id="Crafter",
        title="Investigate failures",
        prompt="Inspect the failing tests and summarize the cause.",
    )

    assert isinstance(result, SynchronousSubagentResult)
    assert result.output == "analysis complete"
    assert result.role_id == "Crafter"
    assert agent_repo.calls[0]["run_id"] == result.run_id
    assert executor.calls[0]["role_id"] == "Crafter"
    assert result.run_id in intent_repo._records
    assert run_control_manager.registered_run_ids == [result.run_id]
    assert run_control_manager.unregistered_run_ids == [result.run_id]
    runtime = runtime_repo.get(result.run_id)
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.COMPLETED
    assert runtime.phase == RunRuntimePhase.TERMINAL


@pytest.mark.asyncio
async def test_background_task_service_run_subagent_with_suppressed_hooks_skips_lifecycle_hooks(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(
        tmp_path / "background-task-service-subagent-sync-suppressed.db"
    )
    runtime_repo = RunRuntimeRepository(
        tmp_path / "background-task-service-subagent-sync-suppressed.db"
    )
    executor = _FakeTaskExecutionService(
        result=TaskExecutionResult(
            output="analysis complete",
            completion_reason=RunCompletionReason.ASSISTANT_RESPONSE,
        )
    )
    hook_service = _CapturingHookService()
    service = BackgroundTaskService(
        background_task_manager=None,
        repository=repo,
        task_execution_service=executor,
        agent_repo=_FakeAgentRepo(),
        task_repo=_FakeTaskRepo(),
        run_intent_repo=_FakeRunIntentRepo(_parent_intent()),
        run_control_manager=_FakeRunControlManager(),
        run_runtime_repo=runtime_repo,
        hook_service=cast(HookService, hook_service),
    )

    result = await service.run_subagent(
        run_id="run-1",
        session_id="session-1",
        workspace_id="workspace-1",
        subagent_role_id="Crafter",
        title="Investigate failures",
        prompt="Inspect the failing tests and summarize the cause.",
        suppress_hooks=True,
    )

    assert result.output == "analysis complete"
    assert hook_service.snapshots == [result.run_id]
    assert hook_service.executed_events == []
    assert hook_service.cleared == [result.run_id]


@pytest.mark.asyncio
async def test_background_task_service_stop_for_run_stops_subagent(
    tmp_path: Path,
) -> None:
    repo = BackgroundTaskRepository(
        tmp_path / "background-task-service-subagent-stop.db"
    )
    runtime_repo = RunRuntimeRepository(
        tmp_path / "background-task-service-subagent-stop.db"
    )
    gate = asyncio.Event()
    executor = _FakeTaskExecutionService(
        result=TaskExecutionResult(output="should not finish"),
        gate=gate,
    )
    service = BackgroundTaskService(
        background_task_manager=None,
        repository=repo,
        task_execution_service=executor,
        agent_repo=_FakeAgentRepo(),
        task_repo=_FakeTaskRepo(),
        run_intent_repo=_FakeRunIntentRepo(_parent_intent()),
        run_control_manager=_FakeRunControlManager(),
        run_runtime_repo=runtime_repo,
    )

    started = await service.start_subagent(
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="MainAgent",
        tool_call_id="call-1",
        workspace_id="workspace-1",
        cwd=Path("C:/workspace"),
        subagent_role_id="Crafter",
        title="Investigate failures",
        prompt="Inspect the failing tests and summarize the cause.",
    )
    stopped = await service.stop_for_run(
        run_id="run-1",
        background_task_id=started.background_task_id,
    )

    persisted = repo.get(started.background_task_id)
    assert stopped.status == BackgroundTaskStatus.STOPPED
    assert stopped.kind == BackgroundTaskKind.SUBAGENT
    assert persisted is not None
    assert persisted.status == BackgroundTaskStatus.STOPPED
    runtime = runtime_repo.get(stopped.subagent_run_id or "")
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.STOPPED
    assert runtime.phase == RunRuntimePhase.IDLE
