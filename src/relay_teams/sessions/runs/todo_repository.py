# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.sessions.runs.todo_models import TodoItem, TodoSnapshot
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class TodoRepository:
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
                CREATE TABLE IF NOT EXISTS run_todos (
                    run_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by_role_id TEXT,
                    updated_by_instance_id TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(run_todos)").fetchall()
            }
            if "updated_by_role_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE run_todos ADD COLUMN updated_by_role_id TEXT"
                )
            if "updated_by_instance_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE run_todos ADD COLUMN updated_by_instance_id TEXT"
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_run_todos_session
                ON run_todos(session_id, updated_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="TodoRepository",
            operation_name="init_tables",
        )

    def upsert(self, snapshot: TodoSnapshot) -> TodoSnapshot:
        def operation() -> None:
            if snapshot.updated_at is None:
                raise ValueError("Todo snapshot updated_at is required for persistence")
            self._conn.execute(
                """
                INSERT INTO run_todos(
                    run_id,
                    session_id,
                    items_json,
                    version,
                    updated_at,
                    updated_by_role_id,
                    updated_by_instance_id
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id)
                DO UPDATE SET
                    session_id=excluded.session_id,
                    items_json=excluded.items_json,
                    version=excluded.version,
                    updated_at=excluded.updated_at,
                    updated_by_role_id=excluded.updated_by_role_id,
                    updated_by_instance_id=excluded.updated_by_instance_id
                """,
                _snapshot_params(snapshot),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="TodoRepository",
            operation_name="upsert",
        )
        persisted = self.get(snapshot.run_id)
        if persisted is None:
            raise RuntimeError(
                f"Failed to persist todo snapshot for run {snapshot.run_id}"
            )
        return persisted

    def get(self, run_id: str) -> TodoSnapshot | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM run_todos
                WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_snapshot_or_none(row)

    def list_by_session(self, session_id: str) -> tuple[TodoSnapshot, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM run_todos
                WHERE session_id=?
                ORDER BY updated_at DESC
                """,
                (session_id,),
            ).fetchall()
        snapshots: list[TodoSnapshot] = []
        for row in rows:
            snapshot = _row_to_snapshot_or_none(row)
            if snapshot is not None:
                snapshots.append(snapshot)
        return tuple(snapshots)

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM run_todos WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="TodoRepository",
            operation_name="delete_by_session",
        )

    def delete_by_run(self, run_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM run_todos WHERE run_id=?",
                (run_id,),
            ),
            lock=self._lock,
            repository_name="TodoRepository",
            operation_name="delete_by_run",
        )


def _snapshot_params(snapshot: TodoSnapshot) -> tuple[object, ...]:
    if snapshot.updated_at is None:
        raise ValueError("Todo snapshot updated_at is required for persistence")
    return (
        snapshot.run_id,
        snapshot.session_id,
        json.dumps(
            [item.model_dump(mode="json") for item in snapshot.items],
            ensure_ascii=False,
        ),
        snapshot.version,
        snapshot.updated_at.isoformat(),
        snapshot.updated_by_role_id,
        snapshot.updated_by_instance_id,
    )


def _row_to_snapshot(row: sqlite3.Row) -> TodoSnapshot:
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if updated_at is None:
        raise ValueError("Invalid persisted todo updated_at")
    return TodoSnapshot(
        run_id=require_persisted_identifier(row["run_id"], field_name="run_id"),
        session_id=require_persisted_identifier(
            row["session_id"], field_name="session_id"
        ),
        items=_decode_items(row["items_json"]),
        version=int(row["version"]),
        updated_at=updated_at,
        updated_by_role_id=normalize_persisted_text(row["updated_by_role_id"]),
        updated_by_instance_id=normalize_persisted_text(row["updated_by_instance_id"]),
    )


def _row_to_snapshot_or_none(row: sqlite3.Row) -> TodoSnapshot | None:
    try:
        return _row_to_snapshot(row)
    except (ValidationError, ValueError) as exc:
        _log_invalid_todo_row(row=row, error=exc)
        return None


def _decode_items(value: object) -> tuple[TodoItem, ...]:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return ()
    decoded = json.loads(normalized)
    if not isinstance(decoded, list):
        raise ValueError("Invalid persisted todo items payload")
    return tuple(TodoItem.model_validate(item) for item in decoded)


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_todo_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "run_id": _persisted_value_preview(row["run_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "items_json": _persisted_value_preview(row["items_json"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.todo_repository.row_invalid",
        message="Skipping invalid persisted todo row",
        payload=payload,
    )
