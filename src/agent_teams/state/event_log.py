from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.core.enums import EventType
from agent_teams.core.models import EventEnvelope, RunEvent
from agent_teams.state.db import open_sqlite


class EventLog:
    """Append-only business event log backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type   TEXT NOT NULL,
                trace_id     TEXT NOT NULL,
                session_id   TEXT NOT NULL,
                task_id      TEXT,
                instance_id  TEXT,
                payload_json TEXT NOT NULL,
                occurred_at  TEXT NOT NULL
            )
            '''
        )
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)'
        )
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)'
        )
        self._conn.commit()

    def emit(self, event: EventEnvelope) -> None:
        self._conn.execute(
            '''
            INSERT INTO events(event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                event.event_type.value,
                event.trace_id,
                event.session_id,
                event.task_id,
                event.instance_id,
                event.payload_json,
                event.occurred_at.isoformat(),
            ),
        )
        self._conn.commit()

    def emit_run_event(self, event: RunEvent) -> None:
        self._conn.execute(
            '''
            INSERT INTO events(event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                event.event_type.value,
                event.trace_id,
                event.session_id,
                event.task_id,
                None, # RunEvent doesn't have instance_id at root
                event.payload_json,
                event.occurred_at.isoformat(),
            ),
        )
        self._conn.commit()

    def list_by_trace(self, trace_id: str) -> tuple[dict, ...]:
        rows = self._conn.execute(
            'SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at '
            'FROM events WHERE trace_id=? ORDER BY id ASC',
            (trace_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[dict, ...]:
        rows = self._conn.execute(
            'SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at '
            'FROM events WHERE session_id=? ORDER BY id ASC',
            (session_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute('DELETE FROM events WHERE session_id=?', (session_id,))
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        import json
        return {
            "event_type": str(row['event_type']),
            "trace_id": str(row['trace_id']),
            "session_id": str(row['session_id']),
            "task_id": str(row['task_id']) if row['task_id'] is not None else None,
            "instance_id": str(row['instance_id']) if row['instance_id'] is not None else None,
            "payload_json": str(row['payload_json']),
            "occurred_at": str(row['occurred_at']),
        }
