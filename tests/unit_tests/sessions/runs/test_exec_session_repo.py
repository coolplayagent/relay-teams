# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.sessions.runs.exec_session_repo import (
    ExecSessionRepository,
)


def test_exec_session_repo_roundtrips_records(tmp_path: Path) -> None:
    db_path = tmp_path / "background-terminals.db"
    repo = ExecSessionRepository(db_path)
    record = ExecSessionRecord(
        exec_session_id="exec-1",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        command="sleep 30",
        cwd="/tmp/project",
        execution_mode="foreground",
        status=ExecSessionStatus.RUNNING,
        recent_output=("booting",),
        output_excerpt="booting",
        log_path="tmp/exec_sessions/exec-1.log",
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


def test_exec_session_repo_marks_transient_terminals_interrupted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-terminals-interrupted.db"
    repo = ExecSessionRepository(db_path)
    running = ExecSessionRecord(
        exec_session_id="exec-running",
        run_id="run-1",
        session_id="session-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=ExecSessionStatus.RUNNING,
        log_path="tmp/exec_sessions/exec-running.log",
    )
    completed = ExecSessionRecord(
        exec_session_id="exec-completed",
        run_id="run-1",
        session_id="session-1",
        command="echo done",
        cwd="/tmp/project",
        status=ExecSessionStatus.COMPLETED,
        log_path="tmp/exec_sessions/exec-completed.log",
    )
    repo.upsert(running)
    repo.upsert(completed)

    affected = repo.mark_transient_exec_sessions_interrupted()

    interrupted = repo.get("exec-running")
    finished = repo.get("exec-completed")
    assert affected == 1
    assert interrupted is not None
    assert interrupted.status == ExecSessionStatus.STOPPED
    assert interrupted.completed_at is not None
    assert finished is not None
    assert finished.status == ExecSessionStatus.COMPLETED
