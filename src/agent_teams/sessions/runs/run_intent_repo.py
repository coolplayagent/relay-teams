from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from agent_teams.sessions.runs.enums import ExecutionMode
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    RunThinkingConfig,
    RunTopologySnapshot,
)
from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.session_models import SessionMode

type _ThinkingEffort = Literal["minimal", "low", "medium", "high"] | None


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
                yolo           TEXT NOT NULL DEFAULT 'false',
                thinking_enabled TEXT NOT NULL DEFAULT 'false',
                thinking_effort TEXT,
                session_mode TEXT NOT NULL DEFAULT 'normal',
                topology_json TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL
            )
            """
        )
        columns = [
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(run_intents)").fetchall()
        ]
        if "yolo" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN yolo TEXT NOT NULL DEFAULT 'false'"
            )
            if "approval_mode" in columns:
                self._conn.execute(
                    """
                    UPDATE run_intents
                    SET yolo = CASE
                        WHEN approval_mode = 'yolo' THEN 'true'
                        ELSE 'false'
                    END
                    """
                )
        if "thinking_enabled" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN thinking_enabled TEXT NOT NULL DEFAULT 'false'"
            )
        if "thinking_effort" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN thinking_effort TEXT"
            )
        if "session_mode" not in columns:
            self._conn.execute(
                "ALTER TABLE run_intents ADD COLUMN session_mode TEXT NOT NULL DEFAULT 'normal'"
            )
        if "topology_json" not in columns:
            self._conn.execute("ALTER TABLE run_intents ADD COLUMN topology_json TEXT")
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
                yolo,
                thinking_enabled,
                thinking_effort,
                session_mode,
                topology_json,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id)
            DO UPDATE SET
                session_id=excluded.session_id,
                intent=excluded.intent,
                execution_mode=excluded.execution_mode,
                yolo=excluded.yolo,
                thinking_enabled=excluded.thinking_enabled,
                thinking_effort=excluded.thinking_effort,
                session_mode=excluded.session_mode,
                topology_json=excluded.topology_json,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                session_id,
                intent.intent,
                intent.execution_mode.value,
                "true" if intent.yolo else "false",
                "true" if intent.thinking.enabled else "false",
                intent.thinking.effort,
                intent.session_mode.value,
                (
                    intent.topology.model_dump_json()
                    if intent.topology is not None
                    else None
                ),
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
            SELECT session_id, intent, execution_mode, yolo, thinking_enabled, thinking_effort, session_mode, topology_json
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
            yolo=str(row["yolo"]).strip().lower() == "true",
            thinking=RunThinkingConfig(
                enabled=str(row["thinking_enabled"]).strip().lower() == "true",
                effort=_coerce_thinking_effort(row["thinking_effort"]),
            ),
            session_mode=SessionMode(str(row["session_mode"] or "normal")),
            topology=_coerce_topology(row["topology_json"]),
        )


def _coerce_thinking_effort(
    value: object,
) -> _ThinkingEffort:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized == "minimal":
        return "minimal"
    if normalized == "low":
        return "low"
    if normalized == "medium":
        return "medium"
    if normalized == "high":
        return "high"
    return None


def _coerce_topology(value: object) -> RunTopologySnapshot | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return RunTopologySnapshot.model_validate_json(value)
