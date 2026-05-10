# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from relay_teams.gateway.discord.models import (
    DiscordInboundQueueRecord,
    DiscordInboundQueueStatus,
)
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)

_NON_TERMINAL_QUEUE_STATUSES = (
    DiscordInboundQueueStatus.QUEUED.value,
    DiscordInboundQueueStatus.STARTING.value,
    DiscordInboundQueueStatus.WAITING_RESULT.value,
)


class DiscordInboundQueueDuplicateError(ValueError):
    def __init__(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_key: str,
    ) -> None:
        self.account_id = account_id
        self.channel_id = channel_id
        self.message_key = message_key
        super().__init__(
            "Duplicate Discord inbound queue record for "
            f"account_id={account_id}, channel_id={channel_id}, "
            f"message_key={message_key}"
        )


class DiscordInboundQueueRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_inbound_queue (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    inbound_queue_id    TEXT NOT NULL UNIQUE,
                    account_id          TEXT NOT NULL,
                    message_key         TEXT NOT NULL,
                    gateway_session_id  TEXT NOT NULL,
                    session_id          TEXT NOT NULL,
                    peer_user_id        TEXT NOT NULL,
                    channel_id          TEXT NOT NULL,
                    guild_id            TEXT,
                    thread_id           TEXT,
                    reply_to_message_id TEXT,
                    text                TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    run_id              TEXT,
                    last_error          TEXT,
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL,
                    completed_at        TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_discord_inbound_queue_message
                ON discord_inbound_queue(account_id, channel_id, message_key)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_inbound_queue_session
                ON discord_inbound_queue(session_id, id ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_discord_inbound_queue_status
                ON discord_inbound_queue(status, id ASC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="DiscordInboundQueueRepository",
            operation_name="init_tables",
        )

    async def create_or_get(
        self,
        record: DiscordInboundQueueRecord,
    ) -> tuple[DiscordInboundQueueRecord, bool]:
        try:
            return await self._create(record), True
        except DiscordInboundQueueDuplicateError:
            return (
                await self.get_by_message_key(
                    account_id=record.account_id,
                    channel_id=record.channel_id,
                    message_key=record.message_key,
                ),
                False,
            )

    async def _create(
        self,
        record: DiscordInboundQueueRecord,
    ) -> DiscordInboundQueueRecord:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                INSERT INTO discord_inbound_queue(
                    inbound_queue_id,
                    account_id,
                    message_key,
                    gateway_session_id,
                    session_id,
                    peer_user_id,
                    channel_id,
                    guild_id,
                    thread_id,
                    reply_to_message_id,
                    text,
                    status,
                    run_id,
                    last_error,
                    created_at,
                    updated_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.inbound_queue_id,
                    record.account_id,
                    record.message_key,
                    record.gateway_session_id,
                    record.session_id,
                    record.peer_user_id,
                    record.channel_id,
                    record.guild_id,
                    record.thread_id,
                    record.reply_to_message_id,
                    record.text,
                    record.status.value,
                    record.run_id,
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    _to_iso(record.completed_at),
                ),
            )
            await cursor.close()

        try:
            await self._run_async_write(
                operation_name="create",
                operation=operation,
            )
        except sqlite3.IntegrityError as exc:
            message = str(exc).lower()
            if (
                "uq_discord_inbound_queue_message" in message
                or "discord_inbound_queue.account_id, discord_inbound_queue.channel_id, discord_inbound_queue.message_key"
                in message
            ):
                raise DiscordInboundQueueDuplicateError(
                    account_id=record.account_id,
                    channel_id=record.channel_id,
                    message_key=record.message_key,
                ) from exc
            raise
        stored = await self.get(record.inbound_queue_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to persist Discord inbound queue {record.inbound_queue_id}"
            )
        return stored

    async def update(
        self,
        record: DiscordInboundQueueRecord,
    ) -> DiscordInboundQueueRecord:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                UPDATE discord_inbound_queue
                SET account_id=?,
                    message_key=?,
                    gateway_session_id=?,
                    session_id=?,
                    peer_user_id=?,
                    channel_id=?,
                    guild_id=?,
                    thread_id=?,
                    reply_to_message_id=?,
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
                    record.channel_id,
                    record.guild_id,
                    record.thread_id,
                    record.reply_to_message_id,
                    record.text,
                    record.status.value,
                    record.run_id,
                    record.last_error,
                    record.updated_at.isoformat(),
                    _to_iso(record.completed_at),
                    record.inbound_queue_id,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="update",
            operation=operation,
        )
        stored = await self.get(record.inbound_queue_id)
        if stored is None:
            raise RuntimeError(
                f"Failed to reload Discord inbound queue {record.inbound_queue_id}"
            )
        return stored

    async def get(self, inbound_queue_id: str) -> DiscordInboundQueueRecord | None:
        async def operation(
            conn: aiosqlite.Connection,
        ) -> DiscordInboundQueueRecord | None:
            row = await async_fetchone(
                conn,
                "SELECT * FROM discord_inbound_queue WHERE inbound_queue_id=?",
                (inbound_queue_id,),
            )
            if row is None:
                return None
            return self._to_record(row)

        return await self._run_async_read(operation)

    async def get_by_message_key(
        self,
        *,
        account_id: str,
        channel_id: str,
        message_key: str,
    ) -> DiscordInboundQueueRecord:
        async def operation(conn: aiosqlite.Connection) -> DiscordInboundQueueRecord:
            row = await async_fetchone(
                conn,
                """
                SELECT *
                FROM discord_inbound_queue
                WHERE account_id=? AND channel_id=? AND message_key=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (account_id, channel_id, message_key),
            )
            if row is None:
                raise KeyError(
                    "Unknown Discord inbound queue record for "
                    f"account_id={account_id}, channel_id={channel_id}, "
                    f"message_key={message_key}"
                )
            return self._to_record(row)

        return await self._run_async_read(operation)

    async def get_latest_by_run_id(
        self,
        run_id: str,
    ) -> DiscordInboundQueueRecord | None:
        if not str(run_id).strip():
            return None

        async def operation(
            conn: aiosqlite.Connection,
        ) -> DiscordInboundQueueRecord | None:
            row = await async_fetchone(
                conn,
                """
                SELECT *
                FROM discord_inbound_queue
                WHERE run_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (run_id,),
            )
            if row is None:
                return None
            return self._to_record(row)

        return await self._run_async_read(operation)

    async def has_non_terminal_item_for_run(self, run_id: str) -> bool:
        if not str(run_id).strip():
            return False

        async def operation(conn: aiosqlite.Connection) -> bool:
            row = await async_fetchone(
                conn,
                f"""
                SELECT 1
                FROM discord_inbound_queue
                WHERE run_id=?
                  AND status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                LIMIT 1
                """,
                (run_id, *_NON_TERMINAL_QUEUE_STATUSES),
            )
            return row is not None

        return await self._run_async_read(operation)

    async def count_non_terminal_ahead(self, inbound_queue_id: str) -> int:
        async def operation(conn: aiosqlite.Connection) -> int:
            row = await async_fetchone(
                conn,
                f"""
                SELECT COUNT(*) AS total
                FROM discord_inbound_queue AS queued
                JOIN discord_inbound_queue AS current
                    ON current.inbound_queue_id=?
                WHERE queued.session_id=current.session_id
                  AND queued.id < current.id
                  AND queued.status IN ({",".join("?" for _ in _NON_TERMINAL_QUEUE_STATUSES)})
                """,
                (inbound_queue_id, *_NON_TERMINAL_QUEUE_STATUSES),
            )
            return int(row["total"]) if row is not None else 0

        return await self._run_async_read(operation)

    async def list_ready_to_start(
        self,
        *,
        limit: int = 20,
        stale_before: datetime | None = None,
    ) -> tuple[DiscordInboundQueueRecord, ...]:
        safe_limit = max(1, min(limit, 100))

        async def operation(
            conn: aiosqlite.Connection,
        ) -> tuple[DiscordInboundQueueRecord, ...]:
            if stale_before is None:
                rows = await async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM discord_inbound_queue
                    WHERE status=?
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (DiscordInboundQueueStatus.QUEUED.value, safe_limit),
                )
            else:
                rows = await async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM discord_inbound_queue
                    WHERE status=?
                       OR (status=? AND updated_at<=?)
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (
                        DiscordInboundQueueStatus.QUEUED.value,
                        DiscordInboundQueueStatus.STARTING.value,
                        stale_before.isoformat(),
                        safe_limit,
                    ),
                )
            return tuple(self._to_record(row) for row in rows)

        return await self._run_async_read(operation)

    async def claim_starting(
        self,
        *,
        inbound_queue_id: str,
        stale_before: datetime,
    ) -> DiscordInboundQueueRecord | None:
        claimed_at = stale_before.isoformat()
        updated_at = datetime.now(tz=timezone.utc).isoformat()

        async def operation(
            conn: aiosqlite.Connection,
        ) -> DiscordInboundQueueRecord | None:
            cursor = await conn.execute(
                """
                UPDATE discord_inbound_queue
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
                    DiscordInboundQueueStatus.STARTING.value,
                    None,
                    updated_at,
                    inbound_queue_id,
                    DiscordInboundQueueStatus.QUEUED.value,
                    DiscordInboundQueueStatus.STARTING.value,
                    claimed_at,
                ),
            )
            rowcount = int(cursor.rowcount or 0)
            await cursor.close()
            if rowcount <= 0:
                return None
            row = await async_fetchone(
                conn,
                "SELECT * FROM discord_inbound_queue WHERE inbound_queue_id=?",
                (inbound_queue_id,),
            )
            return self._to_record(row) if row is not None else None

        return await self._run_async_write(
            operation_name="claim_starting",
            operation=operation,
        )

    async def requeue_if_starting(
        self,
        *,
        inbound_queue_id: str,
        last_error: str | None = None,
    ) -> DiscordInboundQueueRecord | None:
        updated_at = datetime.now(tz=timezone.utc).isoformat()

        async def operation(
            conn: aiosqlite.Connection,
        ) -> DiscordInboundQueueRecord | None:
            cursor = await conn.execute(
                """
                UPDATE discord_inbound_queue
                SET status=?,
                    run_id=?,
                    last_error=?,
                    updated_at=?,
                    completed_at=?
                WHERE inbound_queue_id=?
                  AND status=?
                """,
                (
                    DiscordInboundQueueStatus.QUEUED.value,
                    None,
                    last_error,
                    updated_at,
                    None,
                    inbound_queue_id,
                    DiscordInboundQueueStatus.STARTING.value,
                ),
            )
            rowcount = int(cursor.rowcount or 0)
            await cursor.close()
            if rowcount <= 0:
                return None
            row = await async_fetchone(
                conn,
                "SELECT * FROM discord_inbound_queue WHERE inbound_queue_id=?",
                (inbound_queue_id,),
            )
            return self._to_record(row) if row is not None else None

        return await self._run_async_write(
            operation_name="requeue_if_starting",
            operation=operation,
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> DiscordInboundQueueRecord:
        return DiscordInboundQueueRecord(
            inbound_queue_id=str(row["inbound_queue_id"]),
            account_id=str(row["account_id"]),
            message_key=str(row["message_key"]),
            gateway_session_id=str(row["gateway_session_id"]),
            session_id=str(row["session_id"]),
            peer_user_id=str(row["peer_user_id"]),
            channel_id=str(row["channel_id"]),
            guild_id=str(row["guild_id"]) if row["guild_id"] is not None else None,
            thread_id=str(row["thread_id"]) if row["thread_id"] is not None else None,
            reply_to_message_id=(
                str(row["reply_to_message_id"])
                if row["reply_to_message_id"] is not None
                else None
            ),
            text=str(row["text"]),
            status=DiscordInboundQueueStatus(str(row["status"])),
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
    "DiscordInboundQueueDuplicateError",
    "DiscordInboundQueueRepository",
]
