from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from relay_teams.gateway.wechat.models import (
    WeChatInboundQueueRecord,
    WeChatInboundQueueStatus,
)
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository

_NON_TERMINAL_QUEUE_STATUSES = (
    WeChatInboundQueueStatus.QUEUED.value,
    WeChatInboundQueueStatus.STARTING.value,
    WeChatInboundQueueStatus.WAITING_RESULT.value,
)


class WeChatInboundQueueDuplicateError(ValueError):
    def __init__(self, *, account_id: str, peer_user_id: str, message_key: str) -> None:
        self.account_id = account_id
        self.peer_user_id = peer_user_id
        self.message_key = message_key
        super().__init__(
            "Duplicate WeChat inbound queue record for "
            f"account_id={account_id}, peer_user_id={peer_user_id}, "
            f"message_key={message_key}"
        )


class WeChatInboundQueueRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS wechat_inbound_queue (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    inbound_queue_id  TEXT NOT NULL UNIQUE,
                    account_id        TEXT NOT NULL,
                    message_key       TEXT NOT NULL,
                    gateway_session_id TEXT NOT NULL,
                    session_id        TEXT NOT NULL,
                    peer_user_id      TEXT NOT NULL,
                    context_token     TEXT,
                    text              TEXT NOT NULL,
                    status            TEXT NOT NULL,
                    run_id            TEXT,
                    last_error        TEXT,
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    completed_at      TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_wechat_inbound_queue_message
                ON wechat_inbound_queue(account_id, peer_user_id, message_key)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wechat_inbound_queue_session
                ON wechat_inbound_queue(session_id, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wechat_inbound_queue_status
                ON wechat_inbound_queue(status, id ASC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="WeChatInboundQueueRepository",
            operation_name="init_tables",
        )

    def create_or_get(
        self,
        record: WeChatInboundQueueRecord,
    ) -> tuple[WeChatInboundQueueRecord, bool]:
        try:
            return self._create(record), True
        except WeChatInboundQueueDuplicateError:
            return (
                self.get_by_message_key(
                    account_id=record.account_id,
                    peer_user_id=record.peer_user_id,
                    message_key=record.message_key,
                ),
                False,
            )

    async def create_or_get_async(
        self, record: WeChatInboundQueueRecord
    ) -> tuple[WeChatInboundQueueRecord, bool]:
        return await self._call_sync_async(self.create_or_get, record)

    def _create(
        self,
        record: WeChatInboundQueueRecord,
    ) -> WeChatInboundQueueRecord:
        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO wechat_inbound_queue(
                    inbound_queue_id,
                    account_id,
                    message_key,
                    gateway_session_id,
                    session_id,
                    peer_user_id,
                    context_token,
                    text,
                    status,
                    run_id,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.inbound_queue_id,
                    record.account_id,
                    record.message_key,
                    record.gateway_session_id,
                    record.session_id,
                    record.peer_user_id,
                    record.context_token,
                    record.text,
                    record.status.value,
                    record.run_id,
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    _to_iso(record.completed_at),
                ),
            )

        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=operation,
                lock=self._lock,
                repository_name="WeChatInboundQueueRepository",
                operation_name="create",
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "uq_wechat_inbound_queue_message" in message
                or "wechat_inbound_queue.account_id, wechat_inbound_queue.peer_user_id, wechat_inbound_queue.message_key"
                in message
            ):
                raise WeChatInboundQueueDuplicateError(
                    account_id=record.account_id,
                    peer_user_id=record.peer_user_id,
                    message_key=record.message_key,
                ) from exc
            raise
        stored = self.get(record.inbound_queue_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to persist WeChat inbound queue {record.inbound_queue_id}"
            )
        return stored

    def update(self, record: WeChatInboundQueueRecord) -> WeChatInboundQueueRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE wechat_inbound_queue
                SET account_id=?,
                    message_key=?,
                    gateway_session_id=?,
                    session_id=?,
                    peer_user_id=?,
                    context_token=?,
                    text=?,
                    status=?,
                    run_id=?,
                    last_error=?,
                    updated_at=?,
                    completed_at=?
                WHERE inbound_queue_id=?
                """,
                (
                    record.account_id,
                    record.message_key,
                    record.gateway_session_id,
                    record.session_id,
                    record.peer_user_id,
                    record.context_token,
                    record.text,
                    record.status.value,
                    record.run_id,
                    record.last_error,
                    record.updated_at.isoformat(),
                    _to_iso(record.completed_at),
                    record.inbound_queue_id,
                ),
            ),
            lock=self._lock,
            repository_name="WeChatInboundQueueRepository",
            operation_name="update",
        )
        stored = self.get(record.inbound_queue_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to reload WeChat inbound queue {record.inbound_queue_id}"
            )
        return stored

    async def update_async(
        self, record: WeChatInboundQueueRecord
    ) -> WeChatInboundQueueRecord:
        return await self._call_sync_async(self.update, record)

    def get(self, inbound_queue_id: str) -> WeChatInboundQueueRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM wechat_inbound_queue
                WHERE inbound_queue_id=?
                """,
                (inbound_queue_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    async def get_async(self, inbound_queue_id: str) -> WeChatInboundQueueRecord | None:
        return await self._call_sync_async(self.get, inbound_queue_id)

    def get_by_message_key(
        self,
        *,
        account_id: str,
        peer_user_id: str,
        message_key: str,
    ) -> WeChatInboundQueueRecord:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM wechat_inbound_queue
                WHERE account_id=? AND peer_user_id=? AND message_key=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (account_id, peer_user_id, message_key),
            ).fetchone()
        if row is None:
            raise KeyError(
                "Unknown WeChat inbound queue record for "
                f"account_id={account_id}, peer_user_id={peer_user_id}, "
                f"message_key={message_key}"
            )
        return self._to_record(row)

    async def get_by_message_key_async(
        self, *, account_id: str, peer_user_id: str, message_key: str
    ) -> WeChatInboundQueueRecord:
        return await self._call_sync_async(
            self.get_by_message_key,
            account_id=account_id,
            peer_user_id=peer_user_id,
            message_key=message_key,
        )

    def get_latest_by_run_id(self, run_id: str) -> WeChatInboundQueueRecord | None:
        if not str(run_id).strip():
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM wechat_inbound_queue
                WHERE run_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    async def get_latest_by_run_id_async(
        self, run_id: str
    ) -> WeChatInboundQueueRecord | None:
        return await self._call_sync_async(self.get_latest_by_run_id, run_id)

    def has_non_terminal_item_for_run(self, run_id: str) -> bool:
        if not str(run_id).strip():
            return False
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT 1
                FROM wechat_inbound_queue
                WHERE run_id=?
                  AND status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                LIMIT 1
                """,
                (run_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return row is not None

    async def has_non_terminal_item_for_run_async(self, run_id: str) -> bool:
        return await self._call_sync_async(self.has_non_terminal_item_for_run, run_id)

    def count_non_terminal_by_session(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM wechat_inbound_queue
                WHERE session_id=?
                  AND status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                """,
                (session_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    async def count_non_terminal_by_session_async(self, session_id: str) -> int:
        return await self._call_sync_async(
            self.count_non_terminal_by_session, session_id
        )

    def count_non_terminal_ahead(self, inbound_queue_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS total
                FROM wechat_inbound_queue AS queued
                JOIN wechat_inbound_queue AS current
                    ON current.inbound_queue_id=?
                WHERE queued.session_id=current.session_id
                  AND queued.id < current.id
                  AND queued.status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                """,
                (inbound_queue_id, *_NON_TERMINAL_QUEUE_STATUSES),
            ).fetchone()
        return int(row["total"]) if row is not None else 0

    async def count_non_terminal_ahead_async(self, inbound_queue_id: str) -> int:
        return await self._call_sync_async(
            self.count_non_terminal_ahead, inbound_queue_id
        )

    def list_ready_to_start(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[WeChatInboundQueueRecord, ...]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            if stale_before is None:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM wechat_inbound_queue
                    WHERE status=?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (
                        WeChatInboundQueueStatus.QUEUED.value,
                        safe_limit,
                    ),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT *
                    FROM wechat_inbound_queue
                    WHERE status=?
                       OR (status=? AND updated_at<=?)
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (
                        WeChatInboundQueueStatus.QUEUED.value,
                        WeChatInboundQueueStatus.STARTING.value,
                        stale_before.isoformat(),
                        safe_limit,
                    ),
                ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    async def list_ready_to_start_async(
        self, *, limit: int = 20, stale_before: datetime | None = None
    ) -> tuple[WeChatInboundQueueRecord, ...]:
        return await self._call_sync_async(
            self.list_ready_to_start, limit=limit, stale_before=stale_before
        )

    def claim_starting(
        self,
        *,
        inbound_queue_id: str,
        stale_before: datetime,
    ) -> WeChatInboundQueueRecord | None:
        claimed_at = stale_before.isoformat()
        updated_at = datetime.now(tz=timezone.utc).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                    UPDATE wechat_inbound_queue
                    SET status=?,
                        last_error=?,
                        updated_at=?
                    WHERE inbound_queue_id=?
                      AND (
                        status=?
                        OR (status=? AND updated_at<=?)
                      )
                    """,
                    (
                        WeChatInboundQueueStatus.STARTING.value,
                        None,
                        updated_at,
                        inbound_queue_id,
                        WeChatInboundQueueStatus.QUEUED.value,
                        WeChatInboundQueueStatus.STARTING.value,
                        claimed_at,
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="WeChatInboundQueueRepository",
            operation_name="claim_starting",
        )
        if updated <= 0:
            return None
        return self.get(inbound_queue_id)

    async def claim_starting_async(
        self, *, inbound_queue_id: str, stale_before: datetime
    ) -> WeChatInboundQueueRecord | None:
        return await self._call_sync_async(
            self.claim_starting,
            inbound_queue_id=inbound_queue_id,
            stale_before=stale_before,
        )

    def requeue_if_starting(
        self,
        *,
        inbound_queue_id: str,
        last_error: str | None = None,
    ) -> WeChatInboundQueueRecord | None:
        updated_at = datetime.now(tz=timezone.utc).isoformat()
        updated = run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: (
                self._conn.execute(
                    """
                    UPDATE wechat_inbound_queue
                    SET status=?,
                        run_id=?,
                        last_error=?,
                        updated_at=?,
                        completed_at=?
                    WHERE inbound_queue_id=?
                      AND status=?
                    """,
                    (
                        WeChatInboundQueueStatus.QUEUED.value,
                        None,
                        last_error,
                        updated_at,
                        None,
                        inbound_queue_id,
                        WeChatInboundQueueStatus.STARTING.value,
                    ),
                ).rowcount
            ),
            lock=self._lock,
            repository_name="WeChatInboundQueueRepository",
            operation_name="requeue_if_starting",
        )
        if updated <= 0:
            return None
        return self.get(inbound_queue_id)

    async def requeue_if_starting_async(
        self, *, inbound_queue_id: str, last_error: str | None = None
    ) -> WeChatInboundQueueRecord | None:
        return await self._call_sync_async(
            self.requeue_if_starting,
            inbound_queue_id=inbound_queue_id,
            last_error=last_error,
        )

    def _to_record(self, row: sqlite3.Row) -> WeChatInboundQueueRecord:
        return WeChatInboundQueueRecord(
            inbound_queue_id=str(row["inbound_queue_id"]),
            account_id=str(row["account_id"]),
            message_key=str(row["message_key"]),
            gateway_session_id=str(row["gateway_session_id"]),
            session_id=str(row["session_id"]),
            peer_user_id=str(row["peer_user_id"]),
            context_token=(
                str(row["context_token"]) if row["context_token"] is not None else None
            ),
            text=str(row["text"]),
            status=WeChatInboundQueueStatus(str(row["status"])),
            run_id=str(row["run_id"]) if row["run_id"] is not None else None,
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            completed_at=(
                datetime.fromisoformat(str(row["completed_at"]))
                if row["completed_at"] is not None
                else None
            ),
        )


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "WeChatInboundQueueDuplicateError",
    "WeChatInboundQueueRepository",
]
