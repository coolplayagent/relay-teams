from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.sessions.runs.enums import ApprovalMode, ExecutionMode
from agent_teams.sessions.runs.models import IntentInput
from agent_teams.persistence.db import open_sqlite


class RunIntentRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_intents (
                run_id         TEXT PRIMARY KEY,
                session_id     TEXT NOT NULL,
                intent         TEXT NOT NULL,
                execution_mode TEXT NOT NULL,
                approval_mode  TEXT NOT NULL DEFAULT 'standard',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """
        )
        columns = [
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(run_intents)").fetchall()
        ]
        if "approval_mode" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN approval_mode TEXT NOT NULL DEFAULT 'standard'"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_intents_session ON run_intents(session_id)"
        )
        self._conn.commit()

    def upsert(self, *, run_id: str, session_id: str, intent: IntentInput) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO run_intents(
                run_id,
                session_id,
                intent,
                execution_mode,
                approval_mode,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id)
            DO UPDATE SET
                session_id=excluded.session_id,
                intent=excluded.intent,
                execution_mode=excluded.execution_mode,
                approval_mode=excluded.approval_mode,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                session_id,
                intent.intent,
                intent.execution_mode.value,
                intent.approval_mode.value,
                now,
                now,
            ),
        )
        self._conn.commit()

    def append_followup(self, *, run_id: str, content: str) -> None:
        row = self._conn.execute(
            "SELECT intent FROM run_intents WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        current = str(row["intent"])
        next_intent = f"{current}\n\n{content}" if current.strip() else content
        self._conn.execute(
            """
            UPDATE run_intents
            SET intent=?, updated_at=?
            WHERE run_id=?
            """,
            (next_intent, datetime.now(tz=timezone.utc).isoformat(), run_id),
        )
        self._conn.commit()

    def get(self, run_id: str) -> IntentInput:
        row = self._conn.execute(
            """
            SELECT session_id, intent, execution_mode, approval_mode
            FROM run_intents
            WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        return IntentInput(
            session_id=str(row["session_id"]),
            intent=str(row["intent"]),
            execution_mode=ExecutionMode(str(row["execution_mode"])),
            approval_mode=ApprovalMode(str(row["approval_mode"])),
        )
