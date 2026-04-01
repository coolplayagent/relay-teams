# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.sessions.runs.exec_session_models import (
    ExecSessionRecord,
    ExecSessionStatus,
)
from agent_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)


class ExecSessionRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS exec_sessions (
                    exec_session_id  TEXT PRIMARY KEY,
                    run_id           TEXT NOT NULL,
                    session_id       TEXT NOT NULL,
                    instance_id      TEXT,
                    role_id          TEXT,
                    tool_call_id     TEXT,
                    command          TEXT NOT NULL,
                    cwd              TEXT NOT NULL,
                    execution_mode   TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    tty              INTEGER NOT NULL,
                    timeout_ms       INTEGER,
                    exit_code        INTEGER,
                    recent_output_json TEXT NOT NULL,
                    output_excerpt   TEXT NOT NULL,
                    log_path         TEXT NOT NULL,
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL,
                    completed_at     TEXT,
                    completion_notified_at TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute(
                    "PRAGMA table_info(exec_sessions)"
                ).fetchall()
            }
            if "completion_notified_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE exec_sessions ADD COLUMN completion_notified_at TEXT"
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_exec_sessions_run
                ON exec_sessions(run_id, updated_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_exec_sessions_status
                ON exec_sessions(status, updated_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExecSessionRepository",
            operation_name="init_tables",
        )

    def upsert(self, record: ExecSessionRecord) -> ExecSessionRecord:
        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO exec_sessions(
                    exec_session_id,
                    run_id,
                    session_id,
                    instance_id,
                    role_id,
                    tool_call_id,
                    command,
                    cwd,
                    execution_mode,
                    status,
                    tty,
                    timeout_ms,
                    exit_code,
                    recent_output_json,
                    output_excerpt,
                    log_path,
                    created_at,
                    updated_at,
                    completed_at,
                    completion_notified_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exec_session_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    instance_id=excluded.instance_id,
                    role_id=excluded.role_id,
                    tool_call_id=excluded.tool_call_id,
                    command=excluded.command,
                    cwd=excluded.cwd,
                    execution_mode=excluded.execution_mode,
                    status=excluded.status,
                    tty=excluded.tty,
                    timeout_ms=excluded.timeout_ms,
                    exit_code=excluded.exit_code,
                    recent_output_json=excluded.recent_output_json,
                    output_excerpt=excluded.output_excerpt,
                    log_path=excluded.log_path,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    completed_at=excluded.completed_at,
                    completion_notified_at=excluded.completion_notified_at
                """,
                _record_params(record),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExecSessionRepository",
            operation_name="upsert",
        )
        persisted = self.get(record.exec_session_id)
        if persisted is None:
            raise RuntimeError(
                f"Failed to persist exec session {record.exec_session_id}"
            )
        return persisted

    def get(self, exec_session_id: str) -> ExecSessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM exec_sessions
                WHERE exec_session_id=?
                """,
                (exec_session_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def list_by_run(self, run_id: str) -> tuple[ExecSessionRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM exec_sessions
                WHERE run_id=?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (run_id,),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[ExecSessionRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM exec_sessions
                WHERE session_id=?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (session_id,),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def list_all(self) -> tuple[ExecSessionRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM exec_sessions
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    def delete(self, exec_session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM exec_sessions WHERE exec_session_id=?",
                (exec_session_id,),
            ),
            lock=self._lock,
            repository_name="ExecSessionRepository",
            operation_name="delete",
        )

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM exec_sessions WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="ExecSessionRepository",
            operation_name="delete_by_session",
        )

    def mark_transient_exec_sessions_interrupted(self) -> int:
        affected = 0

        def operation() -> None:
            nonlocal affected
            now = datetime.now(tz=timezone.utc).isoformat()
            cursor = self._conn.execute(
                """
                UPDATE exec_sessions
                SET status=?, updated_at=?, completed_at=COALESCE(completed_at, ?)
                WHERE status IN (?, ?)
                """,
                (
                    ExecSessionStatus.STOPPED.value,
                    now,
                    now,
                    ExecSessionStatus.RUNNING.value,
                    ExecSessionStatus.BLOCKED.value,
                ),
            )
            affected = int(cursor.rowcount or 0)

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExecSessionRepository",
            operation_name="mark_transient_exec_sessions_interrupted",
        )
        return affected


def _record_params(record: ExecSessionRecord) -> tuple[object, ...]:
    return (
        record.exec_session_id,
        record.run_id,
        record.session_id,
        record.instance_id,
        record.role_id,
        record.tool_call_id,
        record.command,
        record.cwd,
        record.execution_mode,
        record.status.value,
        1 if record.tty else 0,
        record.timeout_ms,
        record.exit_code,
        json.dumps(record.recent_output, ensure_ascii=False),
        record.output_excerpt,
        record.log_path,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
        record.completed_at.isoformat() if record.completed_at is not None else None,
        (
            record.completion_notified_at.isoformat()
            if record.completion_notified_at is not None
            else None
        ),
    )


def _row_to_record(row: sqlite3.Row) -> ExecSessionRecord:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("Invalid persisted exec session timestamps")
    return ExecSessionRecord(
        exec_session_id=require_persisted_identifier(
            row["exec_session_id"], field_name="exec_session_id"
        ),
        run_id=require_persisted_identifier(row["run_id"], field_name="run_id"),
        session_id=require_persisted_identifier(
            row["session_id"], field_name="session_id"
        ),
        instance_id=normalize_persisted_text(row["instance_id"]),
        role_id=normalize_persisted_text(row["role_id"]),
        tool_call_id=normalize_persisted_text(row["tool_call_id"]),
        command=str(row["command"]),
        cwd=str(row["cwd"]),
        execution_mode=_decode_execution_mode(row["execution_mode"]),
        status=ExecSessionStatus(str(row["status"])),
        tty=bool(int(row["tty"])),
        timeout_ms=int(row["timeout_ms"]) if row["timeout_ms"] is not None else None,
        exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
        recent_output=_decode_lines(row["recent_output_json"]),
        output_excerpt=str(row["output_excerpt"]),
        log_path=str(row["log_path"]),
        created_at=created_at,
        updated_at=updated_at,
        completed_at=parse_persisted_datetime_or_none(row["completed_at"]),
        completion_notified_at=parse_persisted_datetime_or_none(
            row["completion_notified_at"]
        ),
    )


def _decode_lines(value: object) -> tuple[str, ...]:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return ()
    try:
        decoded = json.loads(normalized)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    result: list[str] = []
    for item in decoded:
        if isinstance(item, str) and item.strip():
            result.append(item)
    return tuple(result)


def _decode_execution_mode(value: object) -> Literal["foreground", "background"]:
    normalized = normalize_persisted_text(value)
    if normalized == "foreground":
        return "foreground"
    return "background"
