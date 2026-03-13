from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.session_models import SessionRecord
from agent_teams.workspace import build_workspace_id


class SessionRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT '',
                metadata   TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = [
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()
        ]
        if "workspace_id" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''"
            )
        self._conn.commit()

    def create(
        self, session_id: str, metadata: dict[str, str] | None = None
    ) -> SessionRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        metadata_dict = metadata or {}
        workspace_id = build_workspace_id(session_id)
        record = SessionRecord(
            session_id=session_id,
            workspace_id=workspace_id,
            metadata=metadata_dict,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

        self._conn.execute(
            """
            INSERT INTO sessions(session_id, workspace_id, metadata, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                record.session_id,
                record.workspace_id,
                json.dumps(record.metadata),
                now,
                now,
            ),
        )
        self._conn.commit()
        return record

    def update_metadata(self, session_id: str, metadata: dict[str, str]) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE sessions
            SET metadata=?, updated_at=?
            WHERE session_id=?
            """,
            (json.dumps(metadata), now, session_id),
        )
        self._conn.commit()

    def get(self, session_id: str) -> SessionRecord:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown session_id: {session_id}")
        return self._to_record(row)

    def list_all(self) -> tuple[SessionRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
        self._conn.commit()

    def _to_record(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=str(row["session_id"]),
            workspace_id=str(
                row["workspace_id"] or build_workspace_id(str(row["session_id"]))
            ),
            metadata=json.loads(str(row["metadata"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
