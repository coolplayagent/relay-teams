# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.gateway.feishu.models import (
    FeishuMessageDeliveryStatus,
    FeishuMessagePoolRecord,
    FeishuMessageProcessingStatus,
)
from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry

_ACTIVE_PROCESSING_STATUSES = (
    FeishuMessageProcessingStatus.QUEUED.value,
    FeishuMessageProcessingStatus.CLAIMED.value,
    FeishuMessageProcessingStatus.WAITING_RESULT.value,
    FeishuMessageProcessingStatus.RETRYABLE_FAILED.value,
)
_VISIBLE_QUEUE_STATUSES = (
    FeishuMessageProcessingStatus.QUEUED.value,
    FeishuMessageProcessingStatus.CLAIMED.value,
    FeishuMessageProcessingStatus.WAITING_RESULT.value,
    FeishuMessageProcessingStatus.RETRYABLE_FAILED.value,
    FeishuMessageProcessingStatus.CANCELLED.value,
    FeishuMessageProcessingStatus.DEAD_LETTER.value,
)


class FeishuMessageDuplicateError(ValueError):
    def __init__(self, trigger_id: str, tenant_key: str, message_key: str) -> None:
        super().__init__(
            "Duplicate Feishu message for "
            f"trigger_id={trigger_id}, tenant_key={tenant_key}, message_key={message_key}"
        )
        self.trigger_id = trigger_id
        self.tenant_key = tenant_key
        self.message_key = message_key


class FeishuMessagePoolRepository:
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
                CREATE TABLE IF NOT EXISTS feishu_message_pool (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_pool_id       TEXT NOT NULL UNIQUE,
                    trigger_id            TEXT NOT NULL,
                    trigger_name          TEXT NOT NULL,
                    tenant_key            TEXT NOT NULL,
                    chat_id               TEXT NOT NULL,
                    chat_type             TEXT NOT NULL,
                    event_id              TEXT NOT NULL,
                    message_key           TEXT NOT NULL,
                    message_id            TEXT,
                    command_name          TEXT,
                    intent_text           TEXT NOT NULL,
                    payload_json          TEXT NOT NULL,
                    metadata_json         TEXT NOT NULL,
                    processing_status     TEXT NOT NULL,
                    ack_status            TEXT NOT NULL,
                    ack_text              TEXT,
                    final_reply_status    TEXT NOT NULL,
                    final_reply_text      TEXT,
                    delivery_count        INTEGER NOT NULL,
                    process_attempts      INTEGER NOT NULL,
                    ack_attempts          INTEGER NOT NULL,
                    final_reply_attempts  INTEGER NOT NULL,
                    session_id            TEXT,
                    run_id                TEXT,
                    next_attempt_at       TEXT NOT NULL,
                    last_claimed_at       TEXT,
                    last_error            TEXT,
                    created_at            TEXT NOT NULL,
                    updated_at            TEXT NOT NULL,
                    completed_at          TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_feishu_message_pool_key
                ON feishu_message_pool(trigger_id, tenant_key, message_key)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_status
                ON feishu_message_pool(processing_status, next_attempt_at, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_chat
                ON feishu_message_pool(trigger_id, tenant_key, chat_id, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feishu_message_pool_run
                ON feishu_message_pool(run_id, updated_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="FeishuMessagePoolRepository",
            operation_name="init_tables",
        )

    def create_or_get(
        self,
        record: FeishuMessagePoolRecord,
    ) -> tuple[FeishuMessagePoolRecord, bool]:
        try:
            created = self._create(record)
        except FeishuMessageDuplicateError:
            existing = self.get_by_message_key(
                trigger_id=record.trigger_id,
                tenant_key=record.tenant_key,
                message_key=record.message_key,
            )
            updated = self.update(
                existing.message_pool_id,
                delivery_count=existing.delivery_count + 1,
                updated_at=datetime.now(tz=timezone.utc),
            )
            return updated, False
        return created, True

    def _create(self, record: FeishuMessagePoolRecord) -> FeishuMessagePoolRecord:
        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO feishu_message_pool(
                    message_pool_id, trigger_id, trigger_name, tenant_key, chat_id,
                    chat_type, event_id, message_key, message_id, command_name,
                    intent_text, payload_json, metadata_json, processing_status,
                    ack_status, ack_text, final_reply_status, final_reply_text,
                    delivery_count, process_attempts, ack_attempts,
                    final_reply_attempts, session_id, run_id, next_attempt_at,
                    last_claimed_at, last_error, created_at, updated_at, completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.message_pool_id,
                    record.trigger_id,
                    record.trigger_name,
                    record.tenant_key,
                    record.chat_id,
                    record.chat_type,
                    record.event_id,
                    record.message_key,
                    record.message_id,
                    record.command_name,
                    record.intent_text,
                    json.dumps(record.payload),
                    json.dumps(record.metadata),
                    record.processing_status.value,
                    record.ack_status.value,
                    record.ack_text,
                    record.final_reply_status.value,
                    record.final_reply_text,
                    record.delivery_count,
                    record.process_attempts,
                    record.ack_attempts,
                    record.final_reply_attempts,
                    record.session_id,
                    record.run_id,
                    record.next_attempt_at.isoformat(),
                    record.last_claimed_at.isoformat()
                    if record.last_claimed_at is not None
                    else None,
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.completed_at.isoformat()
                    if record.completed_at is not None
                    else None,
                ),
            )

        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=operation,
                lock=self._lock,
                repository_name="FeishuMessagePoolRepository",
                operation_name="create",
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "uq_feishu_message_pool_key" in message
                or "feishu_message_pool.trigger_id, feishu_message_pool.tenant_key, feishu_message_pool.message_key"
                in message
            ):
                raise FeishuMessageDuplicateError(
                    record.trigger_id,
                    record.tenant_key,
                    record.message_key,
                ) from exc
            raise
        stored = self.get(record.message_pool_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to persist Feishu message pool record {record.message_pool_id}"
            )
        return stored

    def get(self, message_pool_id: str) -> FeishuMessagePoolRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM feishu_message_pool WHERE message_pool_id=?",
                (message_pool_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_message_key(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        message_key: str,
    ) -> FeishuMessagePoolRecord:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM feishu_message_pool
                WHERE trigger_id=? AND tenant_key=? AND message_key=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (trigger_id, tenant_key, message_key),
            ).fetchone()
        if row is None:
            raise KeyError(
                "Unknown Feishu message pool record for "
                f"trigger_id={trigger_id}, tenant_key={tenant_key}, message_key={message_key}"
            )
        return self._row_to_record(row)

    def get_latest_by_run_id(self, run_id: str) -> FeishuMessagePoolRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM feishu_message_pool
                WHERE run_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def update(
        self,
        message_pool_id: str,
        **changes: object,
    ) -> FeishuMessagePoolRecord:
        current = self.get(message_pool_id)
        if current is None:
            raise KeyError(f"Unknown Feishu message pool id: {message_pool_id}")
        update = dict(changes)
        update.setdefault("updated_at", datetime.now(tz=timezone.utc))
        next_record = current.model_copy(update=update)

        def operation() -> None:
            self._conn.execute(
                """
                UPDATE feishu_message_pool
                SET
                    trigger_id=?,
                    trigger_name=?,
                    tenant_key=?,
                    chat_id=?,
                    chat_type=?,
                    event_id=?,
                    message_key=?,
                    message_id=?,
                    command_name=?,
                    intent_text=?,
                    payload_json=?,
                    metadata_json=?,
                    processing_status=?,
                    ack_status=?,
                    ack_text=?,
                    final_reply_status=?,
                    final_reply_text=?,
                    delivery_count=?,
                    process_attempts=?,
                    ack_attempts=?,
                    final_reply_attempts=?,
                    session_id=?,
                    run_id=?,
                    next_attempt_at=?,
                    last_claimed_at=?,
                    last_error=?,
                    created_at=?,
                    updated_at=?,
                    completed_at=?
                WHERE message_pool_id=?
                """,
                (
                    next_record.trigger_id,
                    next_record.trigger_name,
                    next_record.tenant_key,
                    next_record.chat_id,
                    next_record.chat_type,
                    next_record.event_id,
                    next_record.message_key,
                    next_record.message_id,
                    next_record.command_name,
                    next_record.intent_text,
                    json.dumps(next_record.payload),
                    json.dumps(next_record.metadata),
                    next_record.processing_status.value,
                    next_record.ack_status.value,
                    next_record.ack_text,
                    next_record.final_reply_status.value,
                    next_record.final_reply_text,
                    next_record.delivery_count,
                    next_record.process_attempts,
                    next_record.ack_attempts,
                    next_record.final_reply_attempts,
                    next_record.session_id,
                    next_record.run_id,
                    next_record.next_attempt_at.isoformat(),
                    next_record.last_claimed_at.isoformat()
                    if next_record.last_claimed_at is not None
                    else None,
                    next_record.last_error,
                    next_record.created_at.isoformat(),
                    next_record.updated_at.isoformat(),
                    next_record.completed_at.isoformat()
                    if next_record.completed_at is not None
                    else None,
                    next_record.message_pool_id,
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="FeishuMessagePoolRepository",
            operation_name="update",
        )
        stored = self.get(message_pool_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to reload Feishu message pool record {message_pool_id}"
            )
        return stored

    def count_active_chat_messages_ahead(self, message_pool_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM feishu_message_pool AS queued
                JOIN feishu_message_pool AS current
                    ON current.message_pool_id=?
                WHERE queued.trigger_id=current.trigger_id
                  AND queued.tenant_key=current.tenant_key
                  AND queued.chat_id=current.chat_id
                  AND queued.id < current.id
                  AND queued.processing_status IN ({",".join("?" for _ in _ACTIVE_PROCESSING_STATUSES)})
                """,
                (message_pool_id, *_ACTIVE_PROCESSING_STATUSES),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    def list_ready_for_processing(
        self,
        *,
        ready_at: datetime,
        limit: int = 20,
    ) -> tuple[FeishuMessagePoolRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        active_placeholders = ",".join("?" for _ in _ACTIVE_PROCESSING_STATUSES)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT current.*
                FROM feishu_message_pool AS current
                WHERE current.processing_status IN (?, ?)
                  AND current.next_attempt_at <= ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM feishu_message_pool AS earlier
                      WHERE earlier.trigger_id=current.trigger_id
                        AND earlier.tenant_key=current.tenant_key
                        AND earlier.chat_id=current.chat_id
                        AND earlier.id < current.id
                        AND earlier.processing_status IN ({active_placeholders})
                  )
                ORDER BY current.id ASC
                LIMIT ?
                """,
                (
                    FeishuMessageProcessingStatus.QUEUED.value,
                    FeishuMessageProcessingStatus.RETRYABLE_FAILED.value,
                    ready_at.isoformat(),
                    *_ACTIVE_PROCESSING_STATUSES,
                    safe_limit,
                ),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_waiting_for_result(
        self,
        *,
        limit: int = 20,
    ) -> tuple[FeishuMessagePoolRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM feishu_message_pool
                WHERE processing_status=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (
                    FeishuMessageProcessingStatus.WAITING_RESULT.value,
                    safe_limit,
                ),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_pending_acknowledgements(
        self,
        *,
        limit: int = 20,
    ) -> tuple[FeishuMessagePoolRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM feishu_message_pool
                WHERE ack_status=? AND ack_text IS NOT NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (
                    FeishuMessageDeliveryStatus.PENDING.value,
                    safe_limit,
                ),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def list_active_chat_messages(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> tuple[FeishuMessagePoolRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM feishu_message_pool
                WHERE trigger_id=?
                  AND tenant_key=?
                  AND chat_id=?
                  AND processing_status IN ({",".join("?" for _ in _ACTIVE_PROCESSING_STATUSES)})
                ORDER BY id ASC
                """,
                (trigger_id, tenant_key, chat_id, *_ACTIVE_PROCESSING_STATUSES),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def get_chat_status_counts(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
    ) -> dict[FeishuMessageProcessingStatus, int]:
        counts: dict[FeishuMessageProcessingStatus, int] = {
            status: 0
            for status in (
                FeishuMessageProcessingStatus.QUEUED,
                FeishuMessageProcessingStatus.CLAIMED,
                FeishuMessageProcessingStatus.WAITING_RESULT,
                FeishuMessageProcessingStatus.RETRYABLE_FAILED,
                FeishuMessageProcessingStatus.CANCELLED,
                FeishuMessageProcessingStatus.DEAD_LETTER,
            )
        }
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT processing_status, COUNT(*) AS total
                FROM feishu_message_pool
                WHERE trigger_id=?
                  AND tenant_key=?
                  AND chat_id=?
                  AND processing_status IN ({",".join("?" for _ in _VISIBLE_QUEUE_STATUSES)})
                GROUP BY processing_status
                """,
                (trigger_id, tenant_key, chat_id, *_VISIBLE_QUEUE_STATUSES),
            ).fetchall()
        for row in rows:
            status = FeishuMessageProcessingStatus(str(row["processing_status"]))
            counts[status] = int(row["total"])
        return counts

    def cancel_active_chat_messages(
        self,
        *,
        trigger_id: str,
        tenant_key: str,
        chat_id: str,
        cancelled_at: datetime,
    ) -> int:
        affected = 0

        def operation() -> None:
            nonlocal affected
            cursor = self._conn.execute(
                f"""
                UPDATE feishu_message_pool
                SET
                    processing_status=?,
                    final_reply_status=?,
                    next_attempt_at=?,
                    completed_at=?,
                    updated_at=?,
                    last_error=?
                WHERE trigger_id=?
                  AND tenant_key=?
                  AND chat_id=?
                  AND processing_status IN ({",".join("?" for _ in _ACTIVE_PROCESSING_STATUSES)})
                """,
                (
                    FeishuMessageProcessingStatus.CANCELLED.value,
                    FeishuMessageDeliveryStatus.SKIPPED.value,
                    cancelled_at.isoformat(),
                    cancelled_at.isoformat(),
                    cancelled_at.isoformat(),
                    "cleared_by_user",
                    trigger_id,
                    tenant_key,
                    chat_id,
                    *_ACTIVE_PROCESSING_STATUSES,
                ),
            )
            affected = int(cursor.rowcount or 0)

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="FeishuMessagePoolRepository",
            operation_name="cancel_active_chat_messages",
        )
        return affected

    def recover_stale_claims(self, *, claimed_before: datetime) -> int:
        affected = 0

        def operation() -> None:
            nonlocal affected
            cursor = self._conn.execute(
                """
                UPDATE feishu_message_pool
                SET
                    processing_status=?,
                    updated_at=?,
                    last_claimed_at=NULL
                WHERE processing_status=?
                  AND last_claimed_at IS NOT NULL
                  AND last_claimed_at < ?
                """,
                (
                    FeishuMessageProcessingStatus.QUEUED.value,
                    datetime.now(tz=timezone.utc).isoformat(),
                    FeishuMessageProcessingStatus.CLAIMED.value,
                    claimed_before.isoformat(),
                ),
            )
            affected = int(cursor.rowcount or 0)

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="FeishuMessagePoolRepository",
            operation_name="recover_stale_claims",
        )
        return affected

    def _row_to_record(self, row: sqlite3.Row) -> FeishuMessagePoolRecord:
        return FeishuMessagePoolRecord(
            sequence_id=int(row["id"]),
            message_pool_id=str(row["message_pool_id"]),
            trigger_id=str(row["trigger_id"]),
            trigger_name=str(row["trigger_name"]),
            tenant_key=str(row["tenant_key"]),
            chat_id=str(row["chat_id"]),
            chat_type=str(row["chat_type"]),
            event_id=str(row["event_id"]),
            message_key=str(row["message_key"]),
            message_id=str(row["message_id"])
            if row["message_id"] is not None
            else None,
            command_name=str(row["command_name"])
            if row["command_name"] is not None
            else None,
            intent_text=str(row["intent_text"]),
            payload=json.loads(str(row["payload_json"])),
            metadata=json.loads(str(row["metadata_json"])),
            processing_status=FeishuMessageProcessingStatus(
                str(row["processing_status"])
            ),
            ack_status=FeishuMessageDeliveryStatus(str(row["ack_status"])),
            ack_text=str(row["ack_text"]) if row["ack_text"] is not None else None,
            final_reply_status=FeishuMessageDeliveryStatus(
                str(row["final_reply_status"])
            ),
            final_reply_text=str(row["final_reply_text"])
            if row["final_reply_text"] is not None
            else None,
            delivery_count=int(row["delivery_count"]),
            process_attempts=int(row["process_attempts"]),
            ack_attempts=int(row["ack_attempts"]),
            final_reply_attempts=int(row["final_reply_attempts"]),
            session_id=str(row["session_id"])
            if row["session_id"] is not None
            else None,
            run_id=str(row["run_id"]) if row["run_id"] is not None else None,
            next_attempt_at=datetime.fromisoformat(str(row["next_attempt_at"])),
            last_claimed_at=datetime.fromisoformat(str(row["last_claimed_at"]))
            if row["last_claimed_at"] is not None
            else None,
            last_error=str(row["last_error"])
            if row["last_error"] is not None
            else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=datetime.fromisoformat(str(row["completed_at"]))
            if row["completed_at"] is not None
            else None,
        )
