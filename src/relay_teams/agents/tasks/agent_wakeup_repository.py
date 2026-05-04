# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3

import aiosqlite
from datetime import datetime, timezone
from pathlib import Path

from relay_teams.agents.tasks.enums import TaskTimeoutAction, WakeupStatus
from relay_teams.agents.tasks.wakeup_models import AgentWakeupEntry
from relay_teams.logger import get_logger
from relay_teams.persistence import async_fetchall, async_fetchone
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository

LOGGER = get_logger(__name__)

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS agent_wakeups (
    wakeup_id      TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    trace_id       TEXT NOT NULL,
    session_id     TEXT NOT NULL,
    coalesce_key   TEXT NOT NULL,
    timeout_action TEXT NOT NULL,
    timeout_seconds REAL NOT NULL,
    attempt        INTEGER NOT NULL DEFAULT 1,
    max_attempts   INTEGER NOT NULL DEFAULT 3,
    status         TEXT NOT NULL DEFAULT 'pending',
    enqueued_at    TEXT NOT NULL,
    claimed_at     TEXT,
    completed_at   TEXT
)
"""

_IDX_STATUS_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_wakeups_status "
    "ON agent_wakeups(status, enqueued_at)"
)
_IDX_COALESCE_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wakeups_coalesce "
    "ON agent_wakeups(coalesce_key, status)"
)
_IDX_TASK_SQL = "CREATE INDEX IF NOT EXISTS idx_wakeups_task ON agent_wakeups(task_id)"


class AgentWakeupRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path, repository_name="AgentWakeupRepository")
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.execute(_IDX_STATUS_SQL)
            try:
                self._conn.execute(_IDX_COALESCE_SQL)
            except sqlite3.OperationalError:
                LOGGER.warning("Coalesce index already exists, skipping", exc_info=True)
            self._conn.execute(_IDX_TASK_SQL)

        self._run_write(
            operation_name="init_agent_wakeups_tables",
            operation=operation,
        )

    async def enqueue_async(self, entry: AgentWakeupEntry) -> bool:
        async def _op(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """\
                INSERT OR IGNORE INTO agent_wakeups(
                    wakeup_id, task_id, trace_id, session_id, coalesce_key,
                    timeout_action, timeout_seconds, attempt, max_attempts,
                    status, enqueued_at, claimed_at, completed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.wakeup_id,
                    entry.task_id,
                    entry.trace_id,
                    entry.session_id,
                    entry.coalesce_key,
                    entry.timeout_action.value,
                    entry.timeout_seconds,
                    entry.attempt,
                    entry.max_attempts,
                    entry.status.value,
                    entry.enqueued_at.isoformat(),
                    entry.claimed_at.isoformat() if entry.claimed_at else None,
                    entry.completed_at.isoformat() if entry.completed_at else None,
                ),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            return inserted

        return await self._run_async_write(
            operation_name="enqueue_async",
            operation=_op,
        )

    async def claim_next_pending_async(self) -> AgentWakeupEntry | None:
        async def _op(conn: aiosqlite.Connection) -> AgentWakeupEntry | None:
            row = await async_fetchone(
                conn,
                "SELECT * FROM agent_wakeups "
                "WHERE status = ? ORDER BY enqueued_at ASC LIMIT 1",
                (WakeupStatus.PENDING.value,),
            )
            if row is None:
                return None
            wakeup_id = str(row["wakeup_id"])
            now = datetime.now(tz=timezone.utc).isoformat()
            cursor = await conn.execute(
                "UPDATE agent_wakeups SET status=?, claimed_at=? "
                "WHERE wakeup_id=? AND status=?",
                (
                    WakeupStatus.CLAIMED.value,
                    now,
                    wakeup_id,
                    WakeupStatus.PENDING.value,
                ),
            )
            updated = cursor.rowcount > 0
            await cursor.close()
            if not updated:
                return None
            return _to_entry(row)

        return await self._run_async_write(
            operation_name="claim_next_pending_async",
            operation=_op,
        )

    async def complete_async(self, wakeup_id: str) -> None:
        async def _op(conn: aiosqlite.Connection) -> None:
            now = datetime.now(tz=timezone.utc).isoformat()
            cursor = await conn.execute(
                "UPDATE agent_wakeups SET status=?, completed_at=? WHERE wakeup_id=?",
                (WakeupStatus.COMPLETED.value, now, wakeup_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="complete_async",
            operation=_op,
        )

    async def expire_async(self, wakeup_id: str) -> None:
        async def _op(conn: aiosqlite.Connection) -> None:
            now = datetime.now(tz=timezone.utc).isoformat()
            cursor = await conn.execute(
                "UPDATE agent_wakeups SET status=?, completed_at=? WHERE wakeup_id=?",
                (WakeupStatus.EXPIRED.value, now, wakeup_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="expire_async",
            operation=_op,
        )

    async def list_pending_for_task_async(
        self,
        task_id: str,
    ) -> tuple[AgentWakeupEntry, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT * FROM agent_wakeups "
                "WHERE task_id=? AND status IN (?, ?) "
                "ORDER BY enqueued_at ASC",
                (
                    task_id,
                    WakeupStatus.PENDING.value,
                    WakeupStatus.CLAIMED.value,
                ),
            )
        )
        return tuple(_to_entry(row) for row in rows)

    async def count_pending_async(self) -> int:
        async def _op(conn: aiosqlite.Connection) -> int:
            row = await async_fetchone(
                conn,
                "SELECT COUNT(*) as cnt FROM agent_wakeups WHERE status=?",
                (WakeupStatus.PENDING.value,),
            )
            return int(row["cnt"]) if row is not None else 0

        return await self._run_async_read(_op)


def _to_entry(row: sqlite3.Row) -> AgentWakeupEntry:
    return AgentWakeupEntry(
        wakeup_id=str(row["wakeup_id"]),
        task_id=str(row["task_id"]),
        trace_id=str(row["trace_id"]),
        session_id=str(row["session_id"]),
        coalesce_key=str(row["coalesce_key"]),
        timeout_action=TaskTimeoutAction(str(row["timeout_action"])),
        timeout_seconds=float(row["timeout_seconds"]),
        attempt=int(row["attempt"]),
        max_attempts=int(row["max_attempts"]),
        status=WakeupStatus(str(row["status"])),
        enqueued_at=datetime.fromisoformat(str(row["enqueued_at"])),
        claimed_at=(
            datetime.fromisoformat(str(row["claimed_at"]))
            if row["claimed_at"]
            else None
        ),
        completed_at=(
            datetime.fromisoformat(str(row["completed_at"]))
            if row["completed_at"]
            else None
        ),
    )
