# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, cast

import pytest

from agent_teams.sessions.runs.background_tasks.service import (
    BackgroundTaskService,
)
from agent_teams.sessions.runs.background_tasks.manager import BackgroundTaskManager
from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)


class _FakeBackgroundTaskManager:
    def __init__(self) -> None:
        self._listener: Callable[[BackgroundTaskRecord], Awaitable[None]] | None = None
        self.records: tuple[BackgroundTaskRecord, ...] = ()
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

    async def wait_for_run(
        self,
        *,
        run_id: str,
        background_task_id: str,
        wait_ms: int,
    ) -> tuple[BackgroundTaskRecord, bool]:
        _ = (run_id, background_task_id, wait_ms)
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
        "A managed background task finished. Respond to the user with one short status update"
    )
    assert "<background-task-id>exec-1</background-task-id>" in message
    assert "<status>completed</status>" in message
    assert "done &amp; &lt;ok&gt;" in message


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
    manager.records = (foreground, background)

    records = service.list_for_run("run-1")

    assert tuple(record.background_task_id for record in records) == (
        "exec-background",
    )


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
        wait_ms=1,
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
    from agent_teams.sessions.runs.background_tasks import service as service_module

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
