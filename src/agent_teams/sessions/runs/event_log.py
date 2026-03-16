from __future__ import annotations

from pydantic import JsonValue

import sqlite3
from pathlib import Path

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.run_models import RunEvent
from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.runs.run_state_models import (
    RunStateRecord,
    apply_run_event_to_state,
)
from agent_teams.agents.tasks.events import EventEnvelope


class EventLog:
    """Append-only business event log backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
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
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)"
        )
        self._conn.commit()

    def emit(self, event: EventEnvelope) -> None:
        self._conn.execute(
            """
            INSERT INTO events(event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
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

    def emit_run_event(self, event: RunEvent) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO events(event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
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
        lastrowid = cursor.lastrowid
        if lastrowid is None:
            raise RuntimeError("Failed to persist run event id")
        return int(lastrowid)

    def list_by_trace(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        rows = self._conn.execute(
            "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
            "FROM events WHERE trace_id=? ORDER BY id ASC",
            (trace_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        rows = self._conn.execute(
            "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
            "FROM events WHERE trace_id=? ORDER BY id ASC",
            (trace_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[dict[str, JsonValue], ...]:
        rows = self._conn.execute(
            "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
            "FROM events WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_session_with_ids(
        self, session_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = self._conn.execute(
            "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
            "FROM events WHERE session_id=? ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    def list_run_states(self) -> tuple[RunStateRecord, ...]:
        rows = self._conn.execute(
            "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
            "FROM events ORDER BY id ASC"
        ).fetchall()
        states_by_run: dict[str, RunStateRecord] = {}
        for row in rows:
            row_dict = self._row_to_dict(row)
            try:
                event_type = RunEventType(str(row_dict["event_type"]))
            except ValueError:
                continue
            row_id = row_dict.get("id")
            if not isinstance(row_id, int):
                continue
            run_id = str(row_dict["trace_id"])
            states_by_run[run_id] = apply_run_event_to_state(
                states_by_run.get(run_id),
                event=RunEvent(
                    session_id=str(row_dict["session_id"]),
                    run_id=run_id,
                    trace_id=run_id,
                    task_id=(
                        str(row_dict["task_id"])
                        if row_dict["task_id"] is not None
                        else None
                    ),
                    instance_id=(
                        str(row_dict["instance_id"])
                        if row_dict["instance_id"] is not None
                        else None
                    ),
                    event_type=event_type,
                    payload_json=str(row_dict["payload_json"]),
                ),
                event_id=row_id,
            )
        result = tuple(states_by_run.values())
        return tuple(sorted(result, key=lambda item: item.updated_at, reverse=True))

    def get_run_state(self, run_id: str) -> RunStateRecord | None:
        state: RunStateRecord | None = None
        for row in self.list_by_trace_with_ids(run_id):
            try:
                event_type = RunEventType(str(row["event_type"]))
            except ValueError:
                continue
            row_id = row.get("id")
            if not isinstance(row_id, int):
                continue
            state = apply_run_event_to_state(
                state,
                event=RunEvent(
                    session_id=str(row["session_id"]),
                    run_id=str(row["trace_id"]),
                    trace_id=str(row["trace_id"]),
                    task_id=(
                        str(row["task_id"]) if row["task_id"] is not None else None
                    ),
                    instance_id=(
                        str(row["instance_id"])
                        if row["instance_id"] is not None
                        else None
                    ),
                    event_type=event_type,
                    payload_json=str(row["payload_json"]),
                ),
                event_id=row_id,
            )
        return state

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM events WHERE session_id=?", (session_id,))
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "event_type": str(row["event_type"]),
            "trace_id": str(row["trace_id"]),
            "session_id": str(row["session_id"]),
            "task_id": str(row["task_id"]) if row["task_id"] is not None else None,
            "instance_id": str(row["instance_id"])
            if row["instance_id"] is not None
            else None,
            "payload_json": str(row["payload_json"]),
            "occurred_at": str(row["occurred_at"]),
        }
        if "id" in row.keys():
            result["id"] = int(row["id"])
        return result
