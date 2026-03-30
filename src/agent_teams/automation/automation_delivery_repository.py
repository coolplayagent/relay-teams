# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

from agent_teams.automation.automation_models import (
    AutomationCleanupStatus,
    AutomationDeliveryEvent,
    AutomationDeliveryStatus,
    AutomationFeishuBinding,
    AutomationRunDeliveryRecord,
)
from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry


class AutomationDeliveryRepository:
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
                CREATE TABLE IF NOT EXISTS automation_deliveries (
                    automation_delivery_id TEXT PRIMARY KEY,
                    automation_project_id TEXT NOT NULL,
                    automation_project_name TEXT NOT NULL,
                    run_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    binding_json TEXT NOT NULL,
                    delivery_events_json TEXT NOT NULL,
                    started_status TEXT NOT NULL,
                    terminal_status TEXT NOT NULL,
                    terminal_event TEXT,
                    started_attempts INTEGER NOT NULL,
                    terminal_attempts INTEGER NOT NULL,
                    started_message TEXT,
                    terminal_message TEXT,
                    reply_to_message_id TEXT,
                    started_message_id TEXT,
                    terminal_message_id TEXT,
                    started_sent_at TEXT,
                    terminal_sent_at TEXT,
                    started_cleanup_status TEXT NOT NULL DEFAULT 'skipped',
                    started_cleanup_attempts INTEGER NOT NULL DEFAULT 0,
                    started_cleaned_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_deliveries_project
                ON automation_deliveries(automation_project_id, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_deliveries_started
                ON automation_deliveries(started_status, updated_at ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_deliveries_terminal
                ON automation_deliveries(terminal_status, updated_at ASC)
                """
            )
            self._ensure_column(
                "automation_deliveries",
                "reply_to_message_id",
                "TEXT",
            )
            self._ensure_column(
                "automation_deliveries",
                "started_message_id",
                "TEXT",
            )
            self._ensure_column(
                "automation_deliveries",
                "terminal_message_id",
                "TEXT",
            )
            self._ensure_column(
                "automation_deliveries",
                "started_cleanup_status",
                "TEXT NOT NULL DEFAULT 'skipped'",
            )
            self._ensure_column(
                "automation_deliveries",
                "started_cleanup_attempts",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                "automation_deliveries",
                "started_cleaned_at",
                "TEXT",
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="init_tables",
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in columns):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def create(
        self, record: AutomationRunDeliveryRecord
    ) -> AutomationRunDeliveryRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO automation_deliveries(
                    automation_delivery_id,
                    automation_project_id,
                    automation_project_name,
                    run_id,
                    session_id,
                    reason,
                    binding_json,
                    delivery_events_json,
                    started_status,
                    terminal_status,
                    terminal_event,
                    started_attempts,
                    terminal_attempts,
                    started_message,
                    terminal_message,
                    reply_to_message_id,
                    started_message_id,
                    terminal_message_id,
                    started_sent_at,
                    terminal_sent_at,
                    started_cleanup_status,
                    started_cleanup_attempts,
                    started_cleaned_at,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(record),
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="create",
        )
        return self.get_by_run_id(record.run_id)

    def update(
        self, record: AutomationRunDeliveryRecord
    ) -> AutomationRunDeliveryRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE automation_deliveries
                SET automation_project_id=?,
                    automation_project_name=?,
                    session_id=?,
                    reason=?,
                    binding_json=?,
                    delivery_events_json=?,
                    started_status=?,
                    terminal_status=?,
                    terminal_event=?,
                    started_attempts=?,
                    terminal_attempts=?,
                    started_message=?,
                    terminal_message=?,
                    reply_to_message_id=?,
                    started_message_id=?,
                    terminal_message_id=?,
                    started_sent_at=?,
                    terminal_sent_at=?,
                    started_cleanup_status=?,
                    started_cleanup_attempts=?,
                    started_cleaned_at=?,
                    last_error=?,
                    updated_at=?
                WHERE automation_delivery_id=?
                """,
                (
                    record.automation_project_id,
                    record.automation_project_name,
                    record.session_id,
                    record.reason,
                    _binding_to_json(record.binding),
                    _events_to_json(record.delivery_events),
                    record.started_status.value,
                    record.terminal_status.value,
                    record.terminal_event.value
                    if record.terminal_event is not None
                    else None,
                    record.started_attempts,
                    record.terminal_attempts,
                    record.started_message,
                    record.terminal_message,
                    record.reply_to_message_id,
                    record.started_message_id,
                    record.terminal_message_id,
                    _to_iso(record.started_sent_at),
                    _to_iso(record.terminal_sent_at),
                    record.started_cleanup_status.value,
                    record.started_cleanup_attempts,
                    _to_iso(record.started_cleaned_at),
                    record.last_error,
                    record.updated_at.isoformat(),
                    record.automation_delivery_id,
                ),
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="update",
        )
        return self.get_by_run_id(record.run_id)

    def get_by_run_id(self, run_id: str) -> AutomationRunDeliveryRecord:
        row = self._conn.execute(
            "SELECT * FROM automation_deliveries WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown automation delivery run_id: {run_id}")
        return self._to_record(row)

    def list_pending_started(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[AutomationRunDeliveryRecord, ...]:
        if stale_before is None:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE started_status=?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (AutomationDeliveryStatus.PENDING.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE started_status=?
                   OR (started_status=? AND updated_at<=?)
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (
                    AutomationDeliveryStatus.PENDING.value,
                    AutomationDeliveryStatus.SENDING.value,
                    stale_before.isoformat(),
                    limit,
                ),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_pending_terminal(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[AutomationRunDeliveryRecord, ...]:
        if stale_before is None:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE terminal_status=?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (AutomationDeliveryStatus.PENDING.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE terminal_status=?
                   OR (terminal_status=? AND updated_at<=?)
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (
                    AutomationDeliveryStatus.PENDING.value,
                    AutomationDeliveryStatus.SENDING.value,
                    stale_before.isoformat(),
                    limit,
                ),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def claim_started(
        self,
        *,
        automation_delivery_id: str,
        stale_before: datetime,
    ) -> AutomationRunDeliveryRecord | None:
        claimed_at = stale_before.isoformat()
        updated_at = datetime.now(tz=stale_before.tzinfo).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                UPDATE automation_deliveries
                SET started_status=?,
                    updated_at=?
                WHERE automation_delivery_id=?
                  AND (
                    started_status=?
                    OR (started_status=? AND updated_at<=?)
                  )
                """,
                    (
                        AutomationDeliveryStatus.SENDING.value,
                        updated_at,
                        automation_delivery_id,
                        AutomationDeliveryStatus.PENDING.value,
                        AutomationDeliveryStatus.SENDING.value,
                        claimed_at,
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="claim_started",
        )
        if updated <= 0:
            return None
        row = self._conn.execute(
            "SELECT * FROM automation_deliveries WHERE automation_delivery_id=?",
            (automation_delivery_id,),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def claim_terminal(
        self,
        *,
        automation_delivery_id: str,
        stale_before: datetime,
    ) -> AutomationRunDeliveryRecord | None:
        claimed_at = stale_before.isoformat()
        updated_at = datetime.now(tz=stale_before.tzinfo).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                UPDATE automation_deliveries
                SET terminal_status=?,
                    updated_at=?
                WHERE automation_delivery_id=?
                  AND (
                    terminal_status=?
                    OR (terminal_status=? AND updated_at<=?)
                  )
                """,
                    (
                        AutomationDeliveryStatus.SENDING.value,
                        updated_at,
                        automation_delivery_id,
                        AutomationDeliveryStatus.PENDING.value,
                        AutomationDeliveryStatus.SENDING.value,
                        claimed_at,
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="claim_terminal",
        )
        if updated <= 0:
            return None
        row = self._conn.execute(
            "SELECT * FROM automation_deliveries WHERE automation_delivery_id=?",
            (automation_delivery_id,),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def list_pending_started_cleanup(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[AutomationRunDeliveryRecord, ...]:
        if stale_before is None:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE started_cleanup_status=?
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (AutomationCleanupStatus.PENDING.value, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT *
                FROM automation_deliveries
                WHERE started_cleanup_status=?
                   OR (started_cleanup_status=? AND updated_at<=?)
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (
                    AutomationCleanupStatus.PENDING.value,
                    AutomationCleanupStatus.CLEANING.value,
                    stale_before.isoformat(),
                    limit,
                ),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def claim_started_cleanup(
        self,
        *,
        automation_delivery_id: str,
        stale_before: datetime,
    ) -> AutomationRunDeliveryRecord | None:
        claimed_at = stale_before.isoformat()
        updated_at = datetime.now(tz=stale_before.tzinfo).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                UPDATE automation_deliveries
                SET started_cleanup_status=?,
                    updated_at=?
                WHERE automation_delivery_id=?
                  AND (
                    started_cleanup_status=?
                    OR (started_cleanup_status=? AND updated_at<=?)
                  )
                """,
                    (
                        AutomationCleanupStatus.CLEANING.value,
                        updated_at,
                        automation_delivery_id,
                        AutomationCleanupStatus.PENDING.value,
                        AutomationCleanupStatus.CLEANING.value,
                        claimed_at,
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="claim_started_cleanup",
        )
        if updated <= 0:
            return None
        row = self._conn.execute(
            "SELECT * FROM automation_deliveries WHERE automation_delivery_id=?",
            (automation_delivery_id,),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def delete_by_project(self, automation_project_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM automation_deliveries WHERE automation_project_id=?",
                (automation_project_id,),
            ),
            lock=self._lock,
            repository_name="AutomationDeliveryRepository",
            operation_name="delete_by_project",
        )

    def _to_row(self, record: AutomationRunDeliveryRecord) -> tuple[object, ...]:
        return (
            record.automation_delivery_id,
            record.automation_project_id,
            record.automation_project_name,
            record.run_id,
            record.session_id,
            record.reason,
            _binding_to_json(record.binding),
            _events_to_json(record.delivery_events),
            record.started_status.value,
            record.terminal_status.value,
            record.terminal_event.value if record.terminal_event is not None else None,
            record.started_attempts,
            record.terminal_attempts,
            record.started_message,
            record.terminal_message,
            record.reply_to_message_id,
            record.started_message_id,
            record.terminal_message_id,
            _to_iso(record.started_sent_at),
            _to_iso(record.terminal_sent_at),
            record.started_cleanup_status.value,
            record.started_cleanup_attempts,
            _to_iso(record.started_cleaned_at),
            record.last_error,
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        )

    def _to_record(self, row: sqlite3.Row) -> AutomationRunDeliveryRecord:
        terminal_event_raw = str(row["terminal_event"] or "").strip()
        return AutomationRunDeliveryRecord(
            automation_delivery_id=str(row["automation_delivery_id"]),
            automation_project_id=str(row["automation_project_id"]),
            automation_project_name=str(row["automation_project_name"]),
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            reason=str(row["reason"]),
            binding=_binding_from_json(str(row["binding_json"])),
            delivery_events=_events_from_json(str(row["delivery_events_json"])),
            started_status=AutomationDeliveryStatus(str(row["started_status"])),
            terminal_status=AutomationDeliveryStatus(str(row["terminal_status"])),
            terminal_event=(
                AutomationDeliveryEvent(terminal_event_raw)
                if terminal_event_raw
                else None
            ),
            started_attempts=int(row["started_attempts"] or 0),
            terminal_attempts=int(row["terminal_attempts"] or 0),
            started_message=(
                str(row["started_message"])
                if row["started_message"] is not None
                else None
            ),
            terminal_message=(
                str(row["terminal_message"])
                if row["terminal_message"] is not None
                else None
            ),
            reply_to_message_id=(
                str(row["reply_to_message_id"])
                if row["reply_to_message_id"] is not None
                else None
            ),
            started_message_id=(
                str(row["started_message_id"])
                if row["started_message_id"] is not None
                else None
            ),
            terminal_message_id=(
                str(row["terminal_message_id"])
                if row["terminal_message_id"] is not None
                else None
            ),
            started_sent_at=_from_iso(row["started_sent_at"]),
            terminal_sent_at=_from_iso(row["terminal_sent_at"]),
            started_cleanup_status=AutomationCleanupStatus(
                str(row["started_cleanup_status"])
            ),
            started_cleanup_attempts=int(row["started_cleanup_attempts"] or 0),
            started_cleaned_at=_from_iso(row["started_cleaned_at"]),
            last_error=str(row["last_error"])
            if row["last_error"] is not None
            else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
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


__all__ = ["AutomationDeliveryRepository"]
