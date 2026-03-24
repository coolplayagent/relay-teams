from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.session_models import SessionMode, SessionRecord


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
                session_mode TEXT NOT NULL DEFAULT 'normal',
                normal_root_role_id TEXT,
                orchestration_preset_id TEXT,
                started_at TEXT,
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
        if "session_mode" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'normal'"
            )
        if "normal_root_role_id" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN normal_root_role_id TEXT"
            )
        if "orchestration_preset_id" not in columns:
            self._conn.execute(
                "ALTER TABLE sessions ADD COLUMN orchestration_preset_id TEXT"
            )
        if "started_at" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN started_at TEXT")
        self._conn.commit()

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        metadata: dict[str, str] | None = None,
        session_mode: SessionMode = SessionMode.NORMAL,
        normal_root_role_id: str | None = None,
        orchestration_preset_id: str | None = None,
    ) -> SessionRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        metadata_dict = metadata or {}
        record = SessionRecord(
            session_id=session_id,
            workspace_id=workspace_id,
            metadata=metadata_dict,
            session_mode=session_mode,
            normal_root_role_id=normal_root_role_id,
            orchestration_preset_id=orchestration_preset_id,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

        self._conn.execute(
            """
            INSERT INTO sessions(
                session_id,
                workspace_id,
                metadata,
                session_mode,
                normal_root_role_id,
                orchestration_preset_id,
                started_at,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.session_id,
                record.workspace_id,
                json.dumps(record.metadata),
                record.session_mode.value,
                record.normal_root_role_id,
                record.orchestration_preset_id,
                None,
                now,
                now,
            ),
        )
        self._conn.commit()
        return record

    def update_topology(
        self,
        session_id: str,
        *,
        session_mode: SessionMode,
        normal_root_role_id: str | None,
        orchestration_preset_id: str | None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE sessions
            SET session_mode=?, normal_root_role_id=?, orchestration_preset_id=?, updated_at=?
            WHERE session_id=? AND started_at IS NULL
            """,
            (
                session_mode.value,
                normal_root_role_id,
                orchestration_preset_id,
                now,
                session_id,
            ),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            existing = self.get(session_id)
            if existing.started_at is not None:
                raise RuntimeError("Session mode can no longer be changed")
            raise KeyError(f"Unknown session_id: {session_id}")

    def update_metadata(self, session_id: str, metadata: dict[str, str]) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE sessions
            SET metadata=?, updated_at=?
            WHERE session_id=?
            """,
            (json.dumps(metadata), now, session_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown session_id: {session_id}")

    def mark_started(self, session_id: str) -> SessionRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        cursor = self._conn.execute(
            """
            UPDATE sessions
            SET started_at=COALESCE(started_at, ?), updated_at=?
            WHERE session_id=?
            """,
            (now, now, session_id),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown session_id: {session_id}")
        return self.get(session_id)

    def reconcile_orchestration_presets(
        self,
        *,
        valid_preset_ids: tuple[str, ...],
        default_preset_id: str | None,
    ) -> None:
        valid_ids = set(valid_preset_ids)
        rows = self._conn.execute(
            """
            SELECT session_id, session_mode, orchestration_preset_id, started_at
                 , normal_root_role_id
            FROM sessions
            """
        ).fetchall()
        for row in rows:
            started_at = str(row["started_at"] or "").strip()
            if started_at:
                continue
            session_id = str(row["session_id"])
            session_mode = SessionMode(str(row["session_mode"] or "normal"))
            normal_root_role_id = (
                str(row["normal_root_role_id"] or "").strip() or None
            )
            preset_id = str(row["orchestration_preset_id"] or "").strip() or None
            next_mode = session_mode
            next_normal_root_role_id = normal_root_role_id
            next_preset_id = preset_id
            if preset_id and preset_id not in valid_ids:
                next_preset_id = default_preset_id
            if next_mode == SessionMode.ORCHESTRATION and next_preset_id is None:
                next_mode = SessionMode.NORMAL
            if (
                next_mode != session_mode
                or next_normal_root_role_id != normal_root_role_id
                or next_preset_id != preset_id
            ):
                self.update_topology(
                    session_id,
                    session_mode=next_mode,
                    normal_root_role_id=next_normal_root_role_id,
                    orchestration_preset_id=next_preset_id,
                )

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
            workspace_id=str(row["workspace_id"]),
            metadata=json.loads(str(row["metadata"])),
            session_mode=SessionMode(str(row["session_mode"] or "normal")),
            normal_root_role_id=str(row["normal_root_role_id"] or "").strip() or None,
            orchestration_preset_id=str(row["orchestration_preset_id"] or "").strip()
            or None,
            started_at=(
                datetime.fromisoformat(str(row["started_at"]))
                if row["started_at"] is not None
                else None
            ),
            can_switch_mode=row["started_at"] is None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
