# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from agent_teams.sessions.runs.run_runtime_repo import (
    RunRuntimePhase,
    RunRuntimeRepository,
    RunRuntimeStatus,
)


def test_run_runtime_repo_handles_concurrent_reads_and_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "run_runtime_concurrency.db"
    repo = RunRuntimeRepository(db_path)
    _ = repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )

    errors: list[BaseException] = []
    start_barrier = Barrier(5)

    def writer(worker_id: int) -> None:
        start_barrier.wait()
        try:
            for iteration in range(200):
                _ = repo.update(
                    "run-1",
                    status=(
                        RunRuntimeStatus.RUNNING
                        if iteration % 2 == 0
                        else RunRuntimeStatus.PAUSED
                    ),
                    phase=RunRuntimePhase.COORDINATOR_RUNNING,
                    active_instance_id=f"inst-{worker_id}-{iteration}",
                )
        except BaseException as exc:  # pragma: no cover - regression capture
            errors.append(exc)

    def reader() -> None:
        start_barrier.wait()
        try:
            for _ in range(400):
                records = repo.list_by_session("session-1")
                assert len(records) == 1
                assert records[0].run_id == "run-1"
        except BaseException as exc:  # pragma: no cover - regression capture
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(writer, 1),
            executor.submit(writer, 2),
            executor.submit(reader),
            executor.submit(reader),
            executor.submit(reader),
        ]
        for future in futures:
            future.result()

    assert errors == []
    record = repo.get("run-1")
    assert record is not None
    assert record.session_id == "session-1"
    assert record.root_task_id == "task-1"


def test_run_runtime_repo_marks_transient_runs_interrupted(tmp_path: Path) -> None:
    db_path = tmp_path / "run_runtime_interrupted.db"
    repo = RunRuntimeRepository(db_path)
    _ = repo.ensure(
        run_id="run-running",
        session_id="session-1",
        root_task_id="task-1",
        status=RunRuntimeStatus.RUNNING,
        phase=RunRuntimePhase.COORDINATOR_RUNNING,
    )
    _ = repo.ensure(
        run_id="run-queued",
        session_id="session-1",
        root_task_id="task-2",
        status=RunRuntimeStatus.QUEUED,
        phase=RunRuntimePhase.IDLE,
    )
    _ = repo.ensure(
        run_id="run-paused",
        session_id="session-1",
        root_task_id="task-3",
        status=RunRuntimeStatus.PAUSED,
        phase=RunRuntimePhase.AWAITING_TOOL_APPROVAL,
    )

    affected = repo.mark_transient_runs_interrupted()

    assert affected == 2
    running = repo.get("run-running")
    queued = repo.get("run-queued")
    paused = repo.get("run-paused")
    assert running is not None
    assert queued is not None
    assert paused is not None
    assert running.status == RunRuntimeStatus.STOPPED
    assert running.phase == RunRuntimePhase.IDLE
    assert running.last_error == "interrupted_by_process_restart"
    assert queued.status == RunRuntimeStatus.STOPPED
    assert queued.phase == RunRuntimePhase.IDLE
    assert queued.last_error == "interrupted_by_process_restart"
    assert paused.status == RunRuntimeStatus.PAUSED
    assert paused.phase == RunRuntimePhase.AWAITING_TOOL_APPROVAL


def test_run_runtime_repo_persists_auto_resume_state(tmp_path: Path) -> None:
    db_path = tmp_path / "run_runtime_auto_resume.db"
    repo = RunRuntimeRepository(db_path)
    _ = repo.ensure(
        run_id="run-1",
        session_id="session-1",
        root_task_id="task-1",
    )

    _ = repo.update(
        "run-1",
        auto_resume_attempts=1,
        last_recoverable_error_code="network_stream_interrupted",
    )

    record = repo.get("run-1")
    assert record is not None
    assert record.auto_resume_attempts == 1
    assert record.last_recoverable_error_code == "network_stream_interrupted"
