# -*- coding: utf-8 -*-
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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


def test_run_runtime_repo_skips_invalid_persisted_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "run_runtime_invalid_rows.db"
    repo = RunRuntimeRepository(db_path)
    _ = repo.ensure(
        run_id="run-valid",
        session_id="session-1",
        root_task_id="task-1",
    )
    _insert_run_runtime_row(
        db_path,
        run_id="None",
        session_id="session-1",
    )

    records = repo.list_by_session("session-1")

    assert [record.run_id for record in records] == ["run-valid"]
    assert repo.get("None") is None


def test_run_runtime_repo_get_recovers_invalid_timestamps(tmp_path: Path) -> None:
    db_path = tmp_path / "run_runtime_dirty_timestamps.db"
    repo = RunRuntimeRepository(db_path)
    valid_updated_at = datetime(2025, 1, 3, tzinfo=timezone.utc).isoformat()
    _insert_run_runtime_row(
        db_path,
        run_id="run-dirty",
        session_id="session-1",
        created_at="None",
        updated_at=valid_updated_at,
    )

    loaded = repo.get("run-dirty")

    assert loaded is not None
    assert loaded.run_id == "run-dirty"
    assert loaded.created_at.isoformat() == valid_updated_at
    assert loaded.updated_at.isoformat() == valid_updated_at
    assert repo.list_by_session("session-1") == ()


def test_run_runtime_repo_upsert_recovers_existing_dirty_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "run_runtime_dirty_upsert.db"
    repo = RunRuntimeRepository(db_path)
    _insert_run_runtime_row(
        db_path,
        run_id="run-dirty",
        session_id="session-1",
        created_at="None",
    )

    existing = repo.get("run-dirty")
    assert existing is not None
    updated = repo.upsert(
        existing.model_copy(
            update={
                "status": RunRuntimeStatus.PAUSED,
                "phase": RunRuntimePhase.AWAITING_TOOL_APPROVAL,
            }
        )
    )

    assert updated.status == RunRuntimeStatus.PAUSED
    assert updated.phase == RunRuntimePhase.AWAITING_TOOL_APPROVAL


def _insert_run_runtime_row(
    db_path: Path,
    *,
    run_id: str,
    session_id: str,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> None:
    now = datetime.now(tz=timezone.utc).isoformat()
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        INSERT INTO run_runtime(
            run_id,
            session_id,
            root_task_id,
            status,
            phase,
            active_instance_id,
            active_task_id,
            active_role_id,
            active_subagent_instance_id,
            last_error,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            session_id,
            "task-x",
            RunRuntimeStatus.RUNNING.value,
            RunRuntimePhase.COORDINATOR_RUNNING.value,
            None,
            None,
            None,
            None,
            None,
            created_at or now,
            updated_at or now,
        ),
    )
    connection.commit()
    connection.close()
