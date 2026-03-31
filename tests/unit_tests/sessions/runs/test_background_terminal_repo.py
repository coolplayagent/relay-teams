# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from agent_teams.sessions.runs.background_terminal_models import (
    BackgroundTerminalRecord,
    BackgroundTerminalStatus,
)
from agent_teams.sessions.runs.background_terminal_repo import (
    BackgroundTerminalRepository,
)


def test_background_terminal_repo_roundtrips_records(tmp_path: Path) -> None:
    db_path = tmp_path / "background-terminals.db"
    repo = BackgroundTerminalRepository(db_path)
    record = BackgroundTerminalRecord(
        terminal_id="term-1",
        run_id="run-1",
        session_id="session-1",
        instance_id="inst-1",
        role_id="writer",
        tool_call_id="call-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=BackgroundTerminalStatus.RUNNING,
        recent_output=("booting",),
        stdout_tail=("booting",),
        log_path="tmp/background_terminals/term-1.log",
    )

    persisted = repo.upsert(record)
    loaded = repo.get("term-1")

    assert persisted.terminal_id == "term-1"
    assert loaded is not None
    assert loaded.run_id == "run-1"
    assert loaded.execution_mode == "background"
    assert loaded.stdout_tail == ("booting",)
    assert repo.list_by_run("run-1")[0].terminal_id == "term-1"


def test_background_terminal_repo_marks_transient_terminals_interrupted(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "background-terminals-interrupted.db"
    repo = BackgroundTerminalRepository(db_path)
    running = BackgroundTerminalRecord(
        terminal_id="term-running",
        run_id="run-1",
        session_id="session-1",
        command="sleep 30",
        cwd="/tmp/project",
        status=BackgroundTerminalStatus.RUNNING,
        log_path="tmp/background_terminals/term-running.log",
    )
    completed = BackgroundTerminalRecord(
        terminal_id="term-completed",
        run_id="run-1",
        session_id="session-1",
        command="echo done",
        cwd="/tmp/project",
        status=BackgroundTerminalStatus.COMPLETED,
        log_path="tmp/background_terminals/term-completed.log",
    )
    repo.upsert(running)
    repo.upsert(completed)

    affected = repo.mark_transient_terminals_interrupted()

    interrupted = repo.get("term-running")
    finished = repo.get("term-completed")
    assert affected == 1
    assert interrupted is not None
    assert interrupted.status == BackgroundTerminalStatus.STOPPED
    assert interrupted.completed_at is not None
    assert finished is not None
    assert finished.status == BackgroundTerminalStatus.COMPLETED
