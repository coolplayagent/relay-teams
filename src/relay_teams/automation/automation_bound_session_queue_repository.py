# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from relay_teams.automation.automation_models import (
    AutomationBoundSessionQueueRecord,
    AutomationBoundSessionQueueStatus,
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationFeishuBinding,
    AutomationRunConfig,
)
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry

_NON_TERMINAL_QUEUE_STATUSES = (
    AutomationBoundSessionQueueStatus.QUEUED.value,
    AutomationBoundSessionQueueStatus.STARTING.value,
    AutomationBoundSessionQueueStatus.WAITING_RESULT.value,
)


class AutomationBoundSessionQueueRepository:
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
                CREATE TABLE IF NOT EXISTS automation_bound_session_queue (
                    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_queue_id    TEXT NOT NULL UNIQUE,
                    automation_project_id  TEXT NOT NULL,
                    automation_project_name TEXT NOT NULL,
                    session_id             TEXT NOT NULL,
                    reason                 TEXT NOT NULL,
                    binding_json           TEXT NOT NULL,
                    delivery_events_json   TEXT NOT NULL,
                    run_config_json        TEXT NOT NULL,
                    prompt                 TEXT NOT NULL,
                    queue_message          TEXT NOT NULL,
                    run_id                 TEXT UNIQUE,
                    status                 TEXT NOT NULL,
                    start_attempts         INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at        TEXT NOT NULL,
                    resume_attempts        INTEGER NOT NULL DEFAULT 0,
                    resume_next_attempt_at TEXT NOT NULL,
                    queue_message_id       TEXT,
                    queue_cleanup_status   TEXT NOT NULL DEFAULT 'skipped',
                    queue_cleanup_attempts INTEGER NOT NULL DEFAULT 0,
                    queue_cleaned_at       TEXT,
                    last_error             TEXT,
                    created_at             TEXT NOT NULL,
                    updated_at             TEXT NOT NULL,
                    completed_at           TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_session
                ON automation_bound_session_queue(session_id, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_status
                ON automation_bound_session_queue(status, next_attempt_at, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_bound_session_queue_project
                ON automation_bound_session_queue(automation_project_id, created_at DESC)
                """
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "resume_attempts",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "resume_next_attempt_at",
                "TEXT NOT NULL DEFAULT '1970-01-01T00:00:00+00:00'",
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "queue_message_id",
                "TEXT",
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "queue_cleanup_status",
                "TEXT NOT NULL DEFAULT 'skipped'",
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "queue_cleanup_attempts",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                "automation_bound_session_queue",
                "queue_cleaned_at",
                "TEXT",
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="init_tables",
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in columns):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def create(
        self,
        record: AutomationBoundSessionQueueRecord,
    ) -> AutomationBoundSessionQueueRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO automation_bound_session_queue(
                    automation_queue_id,
                    automation_project_id,
                    automation_project_name,
                    session_id,
                    reason,
                    binding_json,
                    delivery_events_json,
                    run_config_json,
                    prompt,
                    queue_message,
                    run_id,
                    status,
                    start_attempts,
                    next_attempt_at,
                    resume_attempts,
                    resume_next_attempt_at,
                    queue_message_id,
                    queue_cleanup_status,
                    queue_cleanup_attempts,
                    queue_cleaned_at,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(record),
            ),
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="create",
        )
        stored = self.get(record.automation_queue_id)
        if stored is None:
            raise RuntimeError(
                "Failed to persist automation bound session queue record"
            )
        return stored

    def update(
        self,
        record: AutomationBoundSessionQueueRecord,
    ) -> AutomationBoundSessionQueueRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE automation_bound_session_queue
                SET automation_project_id=?,
                    automation_project_name=?,
                    session_id=?,
                    reason=?,
                    binding_json=?,
                    delivery_events_json=?,
                    run_config_json=?,
                    prompt=?,
                    queue_message=?,
                    run_id=?,
                    status=?,
                    start_attempts=?,
                    next_attempt_at=?,
                    resume_attempts=?,
                    resume_next_attempt_at=?,
                    queue_message_id=?,
                    queue_cleanup_status=?,
                    queue_cleanup_attempts=?,
                    queue_cleaned_at=?,
                    last_error=?,
                    updated_at=?,
                    completed_at=?
                WHERE automation_queue_id=?
                """,
                (
                    record.automation_project_id,
                    record.automation_project_name,
                    record.session_id,
                    record.reason,
                    _binding_to_json(record.binding),
                    _events_to_json(record.delivery_events),
                    record.run_config.model_dump_json(),
                    record.prompt,
                    record.queue_message,
                    record.run_id,
                    record.status.value,
                    record.start_attempts,
                    record.next_attempt_at.isoformat(),
                    record.resume_attempts,
                    record.resume_next_attempt_at.isoformat(),
                    record.queue_message_id,
                    record.queue_cleanup_status.value,
                    record.queue_cleanup_attempts,
                    _to_iso(record.queue_cleaned_at),
                    record.last_error,
                    record.updated_at.isoformat(),
                    _to_iso(record.completed_at),
                    record.automation_queue_id,
                ),
            ),
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="update",
        )
        stored = self.get(record.automation_queue_id)
        if stored is None:
            raise RuntimeError("Failed to reload automation bound session queue record")
        return stored

    def get(self, automation_queue_id: str) -> AutomationBoundSessionQueueRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM automation_bound_session_queue
                WHERE automation_queue_id=?
                """,
                (automation_queue_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def has_non_terminal_item_for_run(self, run_id: str) -> bool:
        if not str(run_id).strip():
            return False
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT 1
                FROM automation_bound_session_queue
                WHERE run_id=?
                  AND status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                LIMIT 1
                """,
                (run_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return row is not None

    def count_non_terminal_by_session(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM automation_bound_session_queue
                WHERE session_id=?
                  AND status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                """,
                (session_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def count_non_terminal_ahead(self, automation_queue_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM automation_bound_session_queue AS queued
                JOIN automation_bound_session_queue AS current
                    ON current.automation_queue_id=?
                WHERE queued.session_id=current.session_id
                  AND queued.id < current.id
                  AND queued.status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                """,
                (automation_queue_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def list_ready_to_start(
        self,
        *,
        ready_at: datetime,
        limit: int = 20,
    ) -> tuple[AutomationBoundSessionQueueRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_bound_session_queue
                WHERE status=?
                  AND next_attempt_at<=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (
                    AutomationBoundSessionQueueStatus.QUEUED.value,
                    ready_at.isoformat(),
                    safe_limit,
                ),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_waiting_for_result(
        self,
        *,
        limit: int = 20,
    ) -> tuple[AutomationBoundSessionQueueRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_bound_session_queue
                WHERE status=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (
                    AutomationBoundSessionQueueStatus.WAITING_RESULT.value,
                    safe_limit,
                ),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def claim_starting(
        self,
        *,
        automation_queue_id: str,
        stale_before: datetime,
    ) -> AutomationBoundSessionQueueRecord | None:
        updated_at = datetime.now(tz=stale_before.tzinfo).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                    UPDATE automation_bound_session_queue
                    SET status=?,
                        updated_at=?
                    WHERE automation_queue_id=?
                      AND (
                        status=?
                        OR (status=? AND updated_at<=?)
                      )
                    """,
                    (
                        AutomationBoundSessionQueueStatus.STARTING.value,
                        updated_at,
                        automation_queue_id,
                        AutomationBoundSessionQueueStatus.QUEUED.value,
                        AutomationBoundSessionQueueStatus.STARTING.value,
                        stale_before.isoformat(),
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="claim_starting",
        )
        if updated <= 0:
            return None
        return self.get(automation_queue_id)

    def list_pending_queue_cleanup(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[AutomationBoundSessionQueueRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        if stale_before is None:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM automation_bound_session_queue
                    WHERE queue_cleanup_status=?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (AutomationCleanupStatus.PENDING.value, safe_limit),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM automation_bound_session_queue
                    WHERE queue_cleanup_status=?
                       OR (queue_cleanup_status=? AND updated_at<=?)
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (
                        AutomationCleanupStatus.PENDING.value,
                        AutomationCleanupStatus.CLEANING.value,
                        stale_before.isoformat(),
                        safe_limit,
                    ),
                ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def claim_queue_cleanup(
        self,
        *,
        automation_queue_id: str,
        stale_before: datetime,
    ) -> AutomationBoundSessionQueueRecord | None:
        updated_at = datetime.now(tz=stale_before.tzinfo).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                    UPDATE automation_bound_session_queue
                    SET queue_cleanup_status=?,
                        updated_at=?
                    WHERE automation_queue_id=?
                      AND (
                        queue_cleanup_status=?
                        OR (queue_cleanup_status=? AND updated_at<=?)
                      )
                    """,
                    (
                        AutomationCleanupStatus.CLEANING.value,
                        updated_at,
                        automation_queue_id,
                        AutomationCleanupStatus.PENDING.value,
                        AutomationCleanupStatus.CLEANING.value,
                        stale_before.isoformat(),
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="claim_queue_cleanup",
        )
        if updated <= 0:
            return None
        return self.get(automation_queue_id)

    def has_project_records(self, automation_project_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM automation_bound_session_queue WHERE automation_project_id=? LIMIT 1",
                (automation_project_id,),
            ).fetchone()
        return row is not None

    def delete_by_project(self, automation_project_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                DELETE FROM automation_bound_session_queue
                WHERE automation_project_id=?
                """,
                (automation_project_id,),
            ),
            lock=self._lock,
            repository_name="AutomationBoundSessionQueueRepository",
            operation_name="delete_by_project",
        )

    def _to_row(self, record: AutomationBoundSessionQueueRecord) -> tuple[object, ...]:
        return (
            record.automation_queue_id,
            record.automation_project_id,
            record.automation_project_name,
            record.session_id,
            record.reason,
            _binding_to_json(record.binding),
            _events_to_json(record.delivery_events),
            record.run_config.model_dump_json(),
            record.prompt,
            record.queue_message,
            record.run_id,
            record.status.value,
            record.start_attempts,
            record.next_attempt_at.isoformat(),
            record.resume_attempts,
            record.resume_next_attempt_at.isoformat(),
            record.queue_message_id,
            record.queue_cleanup_status.value,
            record.queue_cleanup_attempts,
            _to_iso(record.queue_cleaned_at),
            record.last_error,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            _to_iso(record.completed_at),
        )

    def _to_record(self, row: sqlite3.Row) -> AutomationBoundSessionQueueRecord:
        return AutomationBoundSessionQueueRecord(
            automation_queue_id=str(row["automation_queue_id"]),
            automation_project_id=str(row["automation_project_id"]),
            automation_project_name=str(row["automation_project_name"]),
            session_id=str(row["session_id"]),
            reason=str(row["reason"]),
            binding=_binding_from_json(str(row["binding_json"])),
            delivery_events=_events_from_json(str(row["delivery_events_json"])),
            run_config=AutomationRunConfig.model_validate_json(
                str(row["run_config_json"])
            ),
            prompt=str(row["prompt"]),
            queue_message=str(row["queue_message"]),
            run_id=str(row["run_id"]) if row["run_id"] is not None else None,
            status=AutomationBoundSessionQueueStatus(str(row["status"])),
            start_attempts=int(row["start_attempts"] or 0),
            next_attempt_at=datetime.fromisoformat(str(row["next_attempt_at"])),
            resume_attempts=int(row["resume_attempts"] or 0),
            resume_next_attempt_at=datetime.fromisoformat(
                str(row["resume_next_attempt_at"])
            ),
            queue_message_id=(
                str(row["queue_message_id"])
                if row["queue_message_id"] is not None
                else None
            ),
            queue_cleanup_status=AutomationCleanupStatus(
                str(row["queue_cleanup_status"])
            ),
            queue_cleanup_attempts=int(row["queue_cleanup_attempts"] or 0),
            queue_cleaned_at=_from_iso(row["queue_cleaned_at"]),
            last_error=str(row["last_error"])
            if row["last_error"] is not None
            else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=_from_iso(row["completed_at"]),
        )


def _binding_to_json(binding: AutomationFeishuBinding) -> str:
    return json.dumps(binding.model_dump(mode="json"))


def _binding_from_json(value: str) -> AutomationFeishuBinding:
    return AutomationFeishuBinding.model_validate(json.loads(value))


def _events_to_json(events: tuple[AutomationDeliveryEvent, ...]) -> str:
    return json.dumps([event.value for event in events])


def _events_from_json(value: str) -> tuple[AutomationDeliveryEvent, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return ()
    return tuple(AutomationDeliveryEvent(str(item)) for item in parsed)


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


__all__ = ["AutomationBoundSessionQueueRepository"]
