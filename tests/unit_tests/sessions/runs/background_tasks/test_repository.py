# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from agent_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from agent_teams.sessions.runs.background_tasks.repository import (
    BackgroundTaskRepository,
)


def test_background_task_repository_roundtrips_records(tmp_path: Path) -> None:
    db_path = tmp_path / "background-terminals.db"
    repo = BackgroundTaskRepository(db_path)
    record = BackgroundTaskRecord(
        exec_session_id="exec-1",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        command="sleep 30",
        cwd="/tmp/project",
        execution_mode="foreground",
        status=BackgroundTaskStatus.RUNNING,
        recent_output=("booting",),
        output_excerpt="booting",
        log_path="tmp/background_tasks/exec-1.log",
        completion_notified_at=datetime.now(tz=timezone.utc),
    )

    persisted = repo.upsert(record)
    loaded = repo.get("exec-1")

    assert persisted.exec_session_id == "exec-1"
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.execution_mode == "foreground"
    assert loaded.output_excerpt == "booting"
    assert loaded.completion_notified_at is not None
    assert repo.list_by_run("run-1")[0].exec_session_id == "exec-1"


def test_background_task_repository_marks_transient_terminals_interrupted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-terminals-interrupted.db"
    repo = BackgroundTaskRepository(db_path)
    running = BackgroundTaskRecord(
        exec_session_id="exec-running",
        run_id="run-1",
        session_id="session-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=BackgroundTaskStatus.RUNNING,
        log_path="tmp/background_tasks/exec-running.log",
    )
    completed = BackgroundTaskRecord(
        exec_session_id="exec-completed",
        run_id="run-1",
        session_id="session-1",
        command="echo done",
        cwd="/tmp/project",
        status=BackgroundTaskStatus.COMPLETED,
        log_path="tmp/background_tasks/exec-completed.log",
    )
    repo.upsert(running)
    repo.upsert(completed)

    affected = repo.mark_transient_background_tasks_interrupted()

    interrupted = repo.get("exec-running")
    finished = repo.get("exec-completed")
    assert affected == 1
    assert interrupted is not None
    assert interrupted.status == BackgroundTaskStatus.STOPPED
    assert interrupted.completed_at is not None
    assert finished is not None
    assert finished.status == BackgroundTaskStatus.COMPLETED


def test_background_task_repository_can_delete_records_by_session(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-terminals-delete-session.db"
    repo = BackgroundTaskRepository(db_path)
    record = BackgroundTaskRecord(
        exec_session_id="exec-1",
        run_id="run-1",
        session_id="session-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=BackgroundTaskStatus.RUNNING,
        log_path="tmp/background_tasks/exec-1.log",
    )
    repo.upsert(record)

    repo.delete_by_session("session-1")

    assert repo.get("exec-1") is None
    assert repo.list_by_session("session-1") == ()
