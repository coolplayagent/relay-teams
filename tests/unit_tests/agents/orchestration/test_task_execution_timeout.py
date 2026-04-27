# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import JsonValue

from relay_teams.agents.orchestration import (
    task_execution_service as task_execution_module,
)
from relay_teams.agents.orchestration.task_contracts import TaskExecutionResult
from relay_teams.agents.instances.enums import InstanceStatus
from relay_teams.agents.tasks.enums import TaskStatus, TaskTimeoutAction
from relay_teams.agents.tasks.events import EventType
from relay_teams.agents.tasks.models import TaskHandoff, TaskLifecyclePolicy
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.runs.recoverable_pause import RecoverableRunPauseError
from relay_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeStatus,
)
from tests.unit_tests.agents.orchestration.test_task_execution_service import (
    _build_service,
    _seed_task,
)


@pytest_asyncio.fixture(autouse=True)
async def _close_async_sqlite_repositories_after_test() -> AsyncIterator[None]:
    yield
    repositories = tuple(
        repository
        for repository in gc.get_objects()
        if isinstance(repository, SharedSqliteRepository)
    )
    for repository in repositories:
        await repository.close_async()


class _SlowProvider:
    async def generate(self, request: object) -> str:
        _ = request
        await asyncio.sleep(1)
        return "late"


class _DelayedProvider:
    async def generate(self, request: object) -> str:
        _ = request
        await asyncio.sleep(0.02)
        return "done"


class _CancelAwareProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def generate(self, request: object) -> str:
        _ = request
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return "late"


class _CancellationResistantProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()

    async def generate(self, request: object) -> str:
        _ = request
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            return "late"
        finally:
            self.finished.set()
        return "late"


@pytest.mark.asyncio
async def test_execute_marks_task_timeout_and_persists_handoff(
    tmp_path: Path,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_timeout.db",
        _SlowProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={
                "lifecycle": TaskLifecyclePolicy(
                    timeout_seconds=0.01,
                    heartbeat_interval_seconds=0.001,
                )
            }
        ),
    ).envelope

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    record = task_repo.get(task.task_id)
    instance = agent_repo.get_instance(instance_id)
    runtime = service.run_runtime_repo.get(task.trace_id)
    events = service.event_bus.list_by_trace(task.trace_id)
    assert result.error_code == "task_timeout"
    assert record.status == TaskStatus.TIMEOUT
    assert instance.status == InstanceStatus.FAILED
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.RUNNING
    assert runtime.phase == RunRuntimePhase.IDLE
    assert record.envelope.handoff is not None
    assert record.envelope.handoff.incomplete == ("query time",)
    assert any(event["event_type"] == EventType.TASK_TIMEOUT.value for event in events)
    assert not any(
        event["event_type"] == EventType.TASK_FAILED.value for event in events
    )


@pytest.mark.asyncio
async def test_execute_timeout_preserves_latest_handoff(
    tmp_path: Path,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_timeout_latest_handoff.db",
        _SlowProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={"lifecycle": TaskLifecyclePolicy(timeout_seconds=0.01)}
        ),
    ).envelope
    _ = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={
                "handoff": TaskHandoff(
                    completed=("drafted implementation",),
                    reason="operator pause",
                )
            }
        ),
    )

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    record = task_repo.get(task.task_id)
    assert result.error_code == "task_timeout"
    assert record.envelope.handoff is not None
    assert record.envelope.handoff.completed == ("drafted implementation",)
    assert record.envelope.handoff.reason == "operator pause"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_phase"),
    [
        (TaskTimeoutAction.RETRY, RunRuntimePhase.AWAITING_RECOVERY),
        (TaskTimeoutAction.HUMAN_GATE, RunRuntimePhase.AWAITING_MANUAL_ACTION),
    ],
)
async def test_execute_timeout_action_leaves_task_recoverable(
    tmp_path: Path,
    action: TaskTimeoutAction,
    expected_phase: RunRuntimePhase,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / f"task_execution_timeout_{action.value}.db",
        _SlowProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={
                "lifecycle": TaskLifecyclePolicy(
                    timeout_seconds=0.01,
                    on_timeout=action,
                )
            }
        ),
    ).envelope

    with pytest.raises(RecoverableRunPauseError) as exc_info:
        _ = await service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )

    record = task_repo.get(task.task_id)
    instance = agent_repo.get_instance(instance_id)
    runtime = service.run_runtime_repo.get(task.trace_id)
    assert exc_info.value.payload.error_code == "task_timeout"
    assert exc_info.value.payload.task_id == task.task_id
    assert exc_info.value.payload.instance_id == instance_id
    assert exc_info.value.payload.role_id == "time"
    assert exc_info.value.payload.runtime_phase == expected_phase
    assert "Task timed out" in exc_info.value.payload.assistant_message
    assert record.status == TaskStatus.STOPPED
    assert instance.status == InstanceStatus.IDLE
    assert runtime is not None
    assert runtime.status == RunRuntimeStatus.PAUSED
    assert runtime.phase == expected_phase
    assert runtime.active_instance_id == instance_id
    assert runtime.active_task_id == task.task_id
    assert runtime.active_role_id == "time"
    assert runtime.active_subagent_instance_id == instance_id
    assert record.error_message is not None
    assert f"on_timeout={action.value}" in record.error_message
    assert record.result is not None
    assert "Task timed out" in record.result


@pytest.mark.asyncio
async def test_execute_cancels_worker_when_caller_cancels_timeout_task(
    tmp_path: Path,
) -> None:
    provider = _CancelAwareProvider()
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_external_cancel.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(update={"lifecycle": TaskLifecyclePolicy(timeout_seconds=60)}),
    ).envelope

    execution = asyncio.create_task(
        service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )
    )
    await provider.started.wait()

    execution.cancel()

    with pytest.raises(asyncio.CancelledError):
        _ = await execution
    assert provider.cancelled.is_set()


@pytest.mark.asyncio
async def test_execute_external_cancel_during_timeout_finalization_keeps_timeout_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _CancelAwareProvider()
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_timeout_finalizer_cancel.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={"lifecycle": TaskLifecyclePolicy(timeout_seconds=0.01)}
        ),
    ).envelope
    original_cancel_and_wait = task_execution_module._cancel_and_wait
    interrupted = False
    execution: asyncio.Task[TaskExecutionResult]

    async def _interrupting_cancel_and_wait(
        task_obj: asyncio.Task[object],
        *,
        suppress_exceptions: bool = False,
        task_name: str = "task",
        timeout_seconds: float | None = None,
        context: Mapping[str, JsonValue] | None = None,
    ) -> object | None:
        nonlocal interrupted
        if task_name == "task_worker" and not interrupted:
            interrupted = True
            execution.cancel()
            await asyncio.sleep(0)
        return await original_cancel_and_wait(
            task_obj,
            suppress_exceptions=suppress_exceptions,
            task_name=task_name,
            timeout_seconds=timeout_seconds,
            context=context,
        )

    monkeypatch.setattr(
        task_execution_module,
        "_cancel_and_wait",
        _interrupting_cancel_and_wait,
    )
    execution = asyncio.create_task(
        service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )
    )

    with pytest.raises(asyncio.CancelledError):
        _ = await execution

    record = task_repo.get(task.task_id)
    assert interrupted is True
    assert record.status == TaskStatus.TIMEOUT
    assert record.error_message is not None
    assert "Task timed out after" in record.error_message


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_execute_applies_timeout_when_worker_cancel_wait_expires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        task_execution_module,
        "TIMEOUT_WORKER_CANCEL_GRACE_SECONDS",
        0.01,
    )
    provider = _CancellationResistantProvider()
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_cancel_wait_timeout.db",
        provider,
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(update={"lifecycle": TaskLifecyclePolicy(timeout_seconds=2.0)}),
    ).envelope

    execution = asyncio.create_task(
        service.execute(
            instance_id=instance_id,
            role_id="time",
            task=task,
        )
    )
    await provider.started.wait()

    result = await execution

    record = task_repo.get(task.task_id)
    assert result.error_code == "task_timeout"
    assert provider.cancelled.is_set()
    assert record.status == TaskStatus.TIMEOUT
    assert record.error_message is not None
    assert "Task timed out after" in record.error_message
    provider.release.set()
    await asyncio.wait_for(provider.finished.wait(), timeout=1.0)
    await asyncio.sleep(0.01)
    assert task_repo.get(task.task_id).status == TaskStatus.TIMEOUT


@pytest.mark.asyncio
async def test_timeout_finalizer_preserves_worker_result_if_cancel_returns_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_timeout_cancel_result.db",
        _SlowProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )

    async def _blocked_worker() -> TaskExecutionResult:
        await asyncio.sleep(60)
        return TaskExecutionResult(output="late")

    worker = asyncio.create_task(_blocked_worker())

    async def _return_result_from_cancel(
        task_obj: asyncio.Task[object],
        *,
        suppress_exceptions: bool = False,
        task_name: str = "task",
        timeout_seconds: float | None = None,
        context: Mapping[str, JsonValue] | None = None,
    ) -> TaskExecutionResult:
        del task_obj, suppress_exceptions, task_name, timeout_seconds, context
        return TaskExecutionResult(output="finished during cancellation")

    monkeypatch.setattr(
        task_execution_module,
        "_cancel_and_wait",
        _return_result_from_cancel,
    )

    try:
        result = await service._complete_timeout_after_worker_cancel_async(
            task=task,
            instance_id=instance_id,
            role_id="time",
            timeout_seconds=0.01,
            worker=worker,
            timeout_cancellation=asyncio.Event(),
        )
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    assert result.output == "finished during cancellation"
    assert result.error_code is None


@pytest.mark.asyncio
async def test_execute_heartbeat_failure_does_not_override_worker_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_heartbeat_failure.db",
        _DelayedProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={"lifecycle": TaskLifecyclePolicy(heartbeat_interval_seconds=0.001)}
        ),
    ).envelope

    async def _fail_heartbeat(
        task_id: str,
        *,
        assigned_instance_id: str | None = None,
    ) -> bool:
        del task_id, assigned_instance_id
        raise RuntimeError("heartbeat unavailable")

    monkeypatch.setattr(task_repo, "heartbeat_running_async", _fail_heartbeat)

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result.output == "done"
    assert task_repo.get(task.task_id).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_heartbeat_continues_after_initial_running_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, task_repo, agent_repo, message_repo = _build_service(
        tmp_path / "task_execution_heartbeat_initial_miss.db",
        _DelayedProvider(),
    )
    task, instance_id = _seed_task(
        task_repo=task_repo,
        agent_repo=agent_repo,
        message_repo=message_repo,
    )
    task = task_repo.update_envelope(
        task.task_id,
        task.model_copy(
            update={"lifecycle": TaskLifecyclePolicy(heartbeat_interval_seconds=0.001)}
        ),
    ).envelope
    original_heartbeat = task_repo.heartbeat_running_async
    calls = 0

    async def _skip_first_heartbeat(
        task_id: str,
        *,
        assigned_instance_id: str | None = None,
    ) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        return await original_heartbeat(
            task_id,
            assigned_instance_id=assigned_instance_id,
        )

    monkeypatch.setattr(task_repo, "heartbeat_running_async", _skip_first_heartbeat)

    result = await service.execute(
        instance_id=instance_id,
        role_id="time",
        task=task,
    )

    assert result.output == "done"
    assert task_repo.get(task.task_id).status == TaskStatus.COMPLETED
    assert calls >= 2
