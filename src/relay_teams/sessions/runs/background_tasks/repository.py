# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import aiosqlite

from relay_teams.persistence import async_fetchall, async_fetchone
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.runs.background_tasks.models import (
    BackgroundTaskKind,
    BackgroundTaskRecord,
    BackgroundTaskStatus,
)
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)


class BackgroundTaskRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS background_tasks (
                    background_task_id  TEXT PRIMARY KEY,
                    run_id           TEXT NOT NULL,
                    session_id       TEXT NOT NULL,
                    kind             TEXT NOT NULL DEFAULT 'command',
                    instance_id      TEXT,
                    role_id          TEXT,
                    tool_call_id     TEXT,
                    title            TEXT NOT NULL DEFAULT '',
                    input_text       TEXT NOT NULL DEFAULT '',
                    command          TEXT NOT NULL,
                    cwd              TEXT NOT NULL,
                    execution_mode   TEXT NOT NULL,
                    status           TEXT NOT NULL,
                    tty              INTEGER NOT NULL,
                    timeout_ms       INTEGER,
                    pid              INTEGER,
                    exit_code        INTEGER,
                    recent_output_json TEXT NOT NULL,
                    output_excerpt   TEXT NOT NULL,
                    log_path         TEXT NOT NULL,
                    subagent_role_id TEXT,
                    subagent_run_id TEXT,
                    subagent_task_id TEXT,
                    subagent_instance_id TEXT,
                    subagent_suppress_hooks INTEGER NOT NULL DEFAULT 0,
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
                    "PRAGMA table_info(background_tasks)"
                ).fetchall()
            }
            if "pid" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN pid INTEGER"
                )
            if "completion_notified_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN completion_notified_at TEXT"
                )
            if "kind" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'command'"
                )
            if "title" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN title TEXT NOT NULL DEFAULT ''"
                )
            if "input_text" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN input_text TEXT NOT NULL DEFAULT ''"
                )
            if "subagent_role_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN subagent_role_id TEXT"
                )
            if "subagent_run_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN subagent_run_id TEXT"
                )
            if "subagent_task_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN subagent_task_id TEXT"
                )
            if "subagent_instance_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN subagent_instance_id TEXT"
                )
            if "subagent_suppress_hooks" not in columns:
                self._conn.execute(
                    "ALTER TABLE background_tasks ADD COLUMN subagent_suppress_hooks INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_background_tasks_run
                ON background_tasks(run_id, updated_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_background_tasks_status
                ON background_tasks(status, updated_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="BackgroundTaskRepository",
            operation_name="init_tables",
        )

    def upsert(self, record: BackgroundTaskRecord) -> BackgroundTaskRecord:
        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO background_tasks(
                    background_task_id,
                    run_id,
                    session_id,
                    kind,
                    instance_id,
                    role_id,
                    tool_call_id,
                    title,
                    input_text,
                    command,
                    cwd,
                    execution_mode,
                    status,
                    tty,
                    timeout_ms,
                    pid,
                    exit_code,
                    recent_output_json,
                    output_excerpt,
                    log_path,
                    subagent_role_id,
                    subagent_run_id,
                    subagent_task_id,
                    subagent_instance_id,
                    subagent_suppress_hooks,
                    created_at,
                    updated_at,
                    completed_at,
                    completion_notified_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(background_task_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    kind=excluded.kind,
                    instance_id=excluded.instance_id,
                    role_id=excluded.role_id,
                    tool_call_id=excluded.tool_call_id,
                    title=excluded.title,
                    input_text=excluded.input_text,
                    command=excluded.command,
                    cwd=excluded.cwd,
                    execution_mode=excluded.execution_mode,
                    status=excluded.status,
                    tty=excluded.tty,
                    timeout_ms=excluded.timeout_ms,
                    pid=excluded.pid,
                    exit_code=excluded.exit_code,
                    recent_output_json=excluded.recent_output_json,
                    output_excerpt=excluded.output_excerpt,
                    log_path=excluded.log_path,
                    subagent_role_id=excluded.subagent_role_id,
                    subagent_run_id=excluded.subagent_run_id,
                    subagent_task_id=excluded.subagent_task_id,
                    subagent_instance_id=excluded.subagent_instance_id,
                    subagent_suppress_hooks=excluded.subagent_suppress_hooks,
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
            repository_name="BackgroundTaskRepository",
            operation_name="upsert",
        )
        persisted = self.get(record.background_task_id)
        if persisted is None:
            raise RuntimeError(
                f"Failed to persist background task {record.background_task_id}"
            )
        return persisted

    async def upsert_async(self, record: BackgroundTaskRecord) -> BackgroundTaskRecord:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                INSERT INTO background_tasks(
                    background_task_id,
                    run_id,
                    session_id,
                    kind,
                    instance_id,
                    role_id,
                    tool_call_id,
                    title,
                    input_text,
                    command,
                    cwd,
                    execution_mode,
                    status,
                    tty,
                    timeout_ms,
                    pid,
                    exit_code,
                    recent_output_json,
                    output_excerpt,
                    log_path,
                    subagent_role_id,
                    subagent_run_id,
                    subagent_task_id,
                    subagent_instance_id,
                    subagent_suppress_hooks,
                    created_at,
                    updated_at,
                    completed_at,
                    completion_notified_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(background_task_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    session_id=excluded.session_id,
                    kind=excluded.kind,
                    instance_id=excluded.instance_id,
                    role_id=excluded.role_id,
                    tool_call_id=excluded.tool_call_id,
                    title=excluded.title,
                    input_text=excluded.input_text,
                    command=excluded.command,
                    cwd=excluded.cwd,
                    execution_mode=excluded.execution_mode,
                    status=excluded.status,
                    tty=excluded.tty,
                    timeout_ms=excluded.timeout_ms,
                    pid=excluded.pid,
                    exit_code=excluded.exit_code,
                    recent_output_json=excluded.recent_output_json,
                    output_excerpt=excluded.output_excerpt,
                    log_path=excluded.log_path,
                    subagent_role_id=excluded.subagent_role_id,
                    subagent_run_id=excluded.subagent_run_id,
                    subagent_task_id=excluded.subagent_task_id,
                    subagent_instance_id=excluded.subagent_instance_id,
                    subagent_suppress_hooks=excluded.subagent_suppress_hooks,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at,
                    completed_at=excluded.completed_at,
                    completion_notified_at=excluded.completion_notified_at
                """,
                _record_params(record),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="upsert_async",
            operation=operation,
        )
        persisted = await self.get_async(record.background_task_id)
        if persisted is None:
            raise RuntimeError(
                f"Failed to persist background task {record.background_task_id}"
            )
        return persisted

    def get(self, background_task_id: str) -> BackgroundTaskRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM background_tasks
                WHERE background_task_id=?
                """,
                (background_task_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    async def get_async(self, background_task_id: str) -> BackgroundTaskRecord | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM background_tasks
                WHERE background_task_id=?
                """,
                (background_task_id,),
            )
        )
        if row is None:
            return None
        return _row_to_record(row)

    def list_by_run(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM background_tasks
                WHERE run_id=?
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (run_id,),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    async def list_by_run_async(self, run_id: str) -> tuple[BackgroundTaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM background_tasks
                WHERE run_id=?
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (run_id,),
            )
        )
        return tuple(_row_to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[BackgroundTaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM background_tasks
                WHERE session_id=?
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (session_id,),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    async def list_by_session_async(
        self, session_id: str
    ) -> tuple[BackgroundTaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM background_tasks
                WHERE session_id=?
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (session_id,),
            )
        )
        return tuple(_row_to_record(row) for row in rows)

    def list_all(self) -> tuple[BackgroundTaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM background_tasks
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    async def list_all_async(self) -> tuple[BackgroundTaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM background_tasks
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
            )
        )
        return tuple(_row_to_record(row) for row in rows)

    def list_interruptible(self) -> tuple[BackgroundTaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM background_tasks
                WHERE status IN (?, ?)
                  AND NOT (kind=? AND execution_mode=?)
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (
                    BackgroundTaskStatus.RUNNING.value,
                    BackgroundTaskStatus.BLOCKED.value,
                    BackgroundTaskKind.SUBAGENT.value,
                    "foreground",
                ),
            ).fetchall()
        return tuple(_row_to_record(row) for row in rows)

    async def list_interruptible_async(self) -> tuple[BackgroundTaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM background_tasks
                WHERE status IN (?, ?)
                  AND NOT (kind=? AND execution_mode=?)
                ORDER BY updated_at DESC, created_at DESC, rowid DESC
                """,
                (
                    BackgroundTaskStatus.RUNNING.value,
                    BackgroundTaskStatus.BLOCKED.value,
                    BackgroundTaskKind.SUBAGENT.value,
                    "foreground",
                ),
            )
        )
        return tuple(_row_to_record(row) for row in rows)

    def delete(self, background_task_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM background_tasks WHERE background_task_id=?",
                (background_task_id,),
            ),
            lock=self._lock,
            repository_name="BackgroundTaskRepository",
            operation_name="delete",
        )

    async def delete_async(self, background_task_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM background_tasks WHERE background_task_id=?",
                (background_task_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_async",
            operation=operation,
        )

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM background_tasks WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="BackgroundTaskRepository",
            operation_name="delete_by_session",
        )

    async def delete_by_session_async(self, session_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM background_tasks WHERE session_id=?",
                (session_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_session_async",
            operation=operation,
        )

    def mark_transient_background_tasks_interrupted(
        self,
        *,
        background_task_ids: tuple[str, ...] | None = None,
    ) -> int:
        if background_task_ids is not None and not background_task_ids:
            return 0

        affected = 0

        def operation() -> None:
            nonlocal affected
            now = datetime.now(tz=timezone.utc).isoformat()
            if background_task_ids is None:
                cursor = self._conn.execute(
                    """
                    UPDATE background_tasks
                    SET status=?, updated_at=?, completed_at=COALESCE(completed_at, ?), pid=NULL
                    WHERE status IN (?, ?)
                      AND NOT (kind=? AND execution_mode=?)
                    """,
                    (
                        BackgroundTaskStatus.STOPPED.value,
                        now,
                        now,
                        BackgroundTaskStatus.RUNNING.value,
                        BackgroundTaskStatus.BLOCKED.value,
                        BackgroundTaskKind.SUBAGENT.value,
                        "foreground",
                    ),
                )
            else:
                placeholders = ", ".join("?" for _ in background_task_ids)
                cursor = self._conn.execute(
                    f"""
                    UPDATE background_tasks
                    SET status=?, updated_at=?, completed_at=COALESCE(completed_at, ?), pid=NULL
                    WHERE background_task_id IN ({placeholders}) AND status IN (?, ?)
                      AND NOT (kind=? AND execution_mode=?)
                    """,
                    (
                        BackgroundTaskStatus.STOPPED.value,
                        now,
                        now,
                        *background_task_ids,
                        BackgroundTaskStatus.RUNNING.value,
                        BackgroundTaskStatus.BLOCKED.value,
                        BackgroundTaskKind.SUBAGENT.value,
                        "foreground",
                    ),
                )
            affected = int(cursor.rowcount or 0)

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="BackgroundTaskRepository",
            operation_name="mark_transient_background_tasks_interrupted",
        )
        return affected

    async def mark_transient_background_tasks_interrupted_async(
        self, *, background_task_ids: tuple[str, ...] | None = None
    ) -> int:
        if background_task_ids is not None and not background_task_ids:
            return 0

        async def operation(conn: aiosqlite.Connection) -> int:
            now = datetime.now(tz=timezone.utc).isoformat()
            if background_task_ids is None:
                cursor = await conn.execute(
                    """
                    UPDATE background_tasks
                    SET status=?, updated_at=?, completed_at=COALESCE(completed_at, ?), pid=NULL
                    WHERE status IN (?, ?)
                    """,
                    (
                        BackgroundTaskStatus.STOPPED.value,
                        now,
                        now,
                        BackgroundTaskStatus.RUNNING.value,
                        BackgroundTaskStatus.BLOCKED.value,
                    ),
                )
            else:
                placeholders = ", ".join("?" for _ in background_task_ids)
                cursor = await conn.execute(
                    f"""
                    UPDATE background_tasks
                    SET status=?, updated_at=?, completed_at=COALESCE(completed_at, ?), pid=NULL
                    WHERE background_task_id IN ({placeholders}) AND status IN (?, ?)
                    """,
                    (
                        BackgroundTaskStatus.STOPPED.value,
                        now,
                        now,
                        *background_task_ids,
                        BackgroundTaskStatus.RUNNING.value,
                        BackgroundTaskStatus.BLOCKED.value,
                    ),
                )
            affected = int(cursor.rowcount or 0)
            await cursor.close()
            return affected

        return await self._run_async_write(
            operation_name="mark_transient_background_tasks_interrupted_async",
            operation=operation,
        )


def _record_params(record: BackgroundTaskRecord) -> tuple[object, ...]:
    return (
        record.background_task_id,
        record.run_id,
        record.session_id,
        record.kind.value,
        record.instance_id,
        record.role_id,
        record.tool_call_id,
        record.title,
        record.input_text,
        record.command,
        record.cwd,
        record.execution_mode,
        record.status.value,
        1 if record.tty else 0,
        record.timeout_ms,
        record.pid,
        record.exit_code,
        json.dumps(record.recent_output, ensure_ascii=False),
        record.output_excerpt,
        record.log_path,
        record.subagent_role_id,
        record.subagent_run_id,
        record.subagent_task_id,
        record.subagent_instance_id,
        1 if record.subagent_suppress_hooks else 0,
        record.created_at.isoformat(),
        record.updated_at.isoformat(),
        record.completed_at.isoformat() if record.completed_at is not None else None,
        (
            record.completion_notified_at.isoformat()
            if record.completion_notified_at is not None
            else None
        ),
    )


def _row_to_record(row: sqlite3.Row) -> BackgroundTaskRecord:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if created_at is None or updated_at is None:
        raise ValueError("Invalid persisted background task timestamps")
    return BackgroundTaskRecord(
        background_task_id=require_persisted_identifier(
            row["background_task_id"], field_name="background_task_id"
        ),
        run_id=require_persisted_identifier(row["run_id"], field_name="run_id"),
        session_id=require_persisted_identifier(
            row["session_id"], field_name="session_id"
        ),
        kind=BackgroundTaskKind(str(row["kind"] or BackgroundTaskKind.COMMAND.value)),
        instance_id=normalize_persisted_text(row["instance_id"]),
        role_id=normalize_persisted_text(row["role_id"]),
        tool_call_id=normalize_persisted_text(row["tool_call_id"]),
        title=str(row["title"] or ""),
        input_text=str(row["input_text"] or ""),
        command=str(row["command"]),
        cwd=str(row["cwd"]),
        execution_mode=_decode_execution_mode(row["execution_mode"]),
        status=BackgroundTaskStatus(str(row["status"])),
        tty=bool(int(row["tty"])),
        timeout_ms=int(row["timeout_ms"]) if row["timeout_ms"] is not None else None,
        pid=int(row["pid"]) if row["pid"] is not None else None,
        exit_code=int(row["exit_code"]) if row["exit_code"] is not None else None,
        recent_output=_decode_lines(row["recent_output_json"]),
        output_excerpt=str(row["output_excerpt"]),
        log_path=str(row["log_path"]),
        subagent_role_id=normalize_persisted_text(row["subagent_role_id"]),
        subagent_run_id=normalize_persisted_text(row["subagent_run_id"]),
        subagent_task_id=normalize_persisted_text(row["subagent_task_id"]),
        subagent_instance_id=normalize_persisted_text(row["subagent_instance_id"]),
        subagent_suppress_hooks=bool(int(row["subagent_suppress_hooks"] or 0)),
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
