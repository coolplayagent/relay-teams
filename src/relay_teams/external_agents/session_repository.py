from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from relay_teams.external_agents.models import ExternalAgentSessionRecord
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry


class ExternalAgentSessionRepository:
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
                CREATE TABLE IF NOT EXISTS external_agent_sessions (
                    session_id          TEXT NOT NULL,
                    role_id             TEXT NOT NULL,
                    agent_id            TEXT NOT NULL,
                    transport           TEXT NOT NULL,
                    external_session_id TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL,
                    PRIMARY KEY(session_id, role_id, agent_id)
                )
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExternalAgentSessionRepository",
            operation_name="init_tables",
        )

    def get(
        self,
        *,
        session_id: str,
        role_id: str,
        agent_id: str,
    ) -> ExternalAgentSessionRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM external_agent_sessions
                WHERE session_id=? AND role_id=? AND agent_id=?
                """,
                (session_id, role_id, agent_id),
            ).fetchone()
        if row is None:
            return None
        return ExternalAgentSessionRecord.model_validate(dict(row))

    def upsert(self, record: ExternalAgentSessionRecord) -> ExternalAgentSessionRecord:
        next_record = record.model_copy(
            update={"updated_at": datetime.now(tz=timezone.utc)}
        )

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO external_agent_sessions(
                    session_id,
                    role_id,
                    agent_id,
                    transport,
                    external_session_id,
                    status,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, role_id, agent_id)
                DO UPDATE SET
                    transport=excluded.transport,
                    external_session_id=excluded.external_session_id,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    next_record.session_id,
                    next_record.role_id,
                    next_record.agent_id,
                    next_record.transport.value,
                    next_record.external_session_id,
                    next_record.status.value,
                    next_record.created_at.isoformat(),
                    next_record.updated_at.isoformat(),
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExternalAgentSessionRepository",
            operation_name="upsert",
        )
        persisted = self.get(
            session_id=next_record.session_id,
            role_id=next_record.role_id,
            agent_id=next_record.agent_id,
        )
        if persisted is None:
            raise RuntimeError("Failed to persist external agent session")
        return persisted

    def delete(self, *, session_id: str, role_id: str, agent_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                DELETE FROM external_agent_sessions
                WHERE session_id=? AND role_id=? AND agent_id=?
                """,
                (session_id, role_id, agent_id),
            ),
            lock=self._lock,
            repository_name="ExternalAgentSessionRepository",
            operation_name="delete",
        )
