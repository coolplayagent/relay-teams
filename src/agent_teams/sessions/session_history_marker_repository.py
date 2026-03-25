# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerRecord,
    SessionHistoryMarkerType,
)


class SessionHistoryMarkerRepository:
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
                CREATE TABLE IF NOT EXISTS session_history_markers (
                    marker_id      TEXT PRIMARY KEY,
                    session_id     TEXT NOT NULL,
                    marker_type    TEXT NOT NULL,
                    metadata_json  TEXT NOT NULL DEFAULT '{}',
                    created_at     TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_history_markers_session
                ON session_history_markers(session_id, created_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="init_tables",
        )

    def create(
        self,
        *,
        session_id: str,
        marker_type: SessionHistoryMarkerType,
        metadata: dict[str, str] | None = None,
    ) -> SessionHistoryMarkerRecord:
        now = datetime.now(tz=timezone.utc)
        record = SessionHistoryMarkerRecord(
            marker_id=f"marker-{uuid.uuid4().hex}",
            session_id=session_id,
            marker_type=marker_type,
            metadata={} if metadata is None else dict(metadata),
            created_at=now,
        )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO session_history_markers(
                    marker_id,
                    session_id,
                    marker_type,
                    metadata_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    record.marker_id,
                    record.session_id,
                    record.marker_type.value,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            ),
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="create",
        )
        return record

    def create_clear_marker(self, session_id: str) -> SessionHistoryMarkerRecord:
        return self.create(
            session_id=session_id,
            marker_type=SessionHistoryMarkerType.CLEAR,
        )

    def list_by_session(
        self,
        session_id: str,
    ) -> tuple[SessionHistoryMarkerRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT marker_id, session_id, marker_type, metadata_json, created_at
                FROM session_history_markers
                WHERE session_id=?
                ORDER BY created_at ASC, marker_id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def get_latest(
        self,
        session_id: str,
        *,
        marker_type: SessionHistoryMarkerType | None = None,
    ) -> SessionHistoryMarkerRecord | None:
        query = """
            SELECT marker_id, session_id, marker_type, metadata_json, created_at
            FROM session_history_markers
            WHERE session_id=?
            """
        params: tuple[str, ...]
        if marker_type is None:
            params = (session_id,)
        else:
            query += " AND marker_type=?"
            params = (session_id, marker_type.value)
        query += " ORDER BY created_at DESC, marker_id DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM session_history_markers WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="delete_by_session",
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> SessionHistoryMarkerRecord:
        metadata = _load_metadata(str(row["metadata_json"]))
        return SessionHistoryMarkerRecord(
            marker_id=str(row["marker_id"]),
            session_id=str(row["session_id"]),
            marker_type=SessionHistoryMarkerType(str(row["marker_type"])),
            metadata=metadata,
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )


def _load_metadata(raw_value: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if isinstance(key, str) and isinstance(value, str)
    }
