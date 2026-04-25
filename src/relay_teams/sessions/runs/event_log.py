from __future__ import annotations

from pydantic import JsonValue

import sqlite3
from pathlib import Path

from relay_teams.persistence import async_fetchall
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.runs.run_state_models import (
    RunStateRecord,
    apply_run_event_to_state,
)
from relay_teams.sessions.runs.enums import RunEventType
from relay_teams.sessions.runs.run_models import RunEvent
from relay_teams.agents.tasks.events import EventEnvelope


class EventLog(SharedSqliteRepository):
    """Append-only business event log backed by SQLite."""

    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
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

        self._run_write(
            operation_name="init_tables",
            operation=operation,
        )

    async def _init_tables_async(self) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
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
            await cursor.close()
            cursor = await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)"
            )
            await cursor.close()
            cursor = await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)"
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="init_tables_async",
            operation=lambda _conn: operation(),
        )

    def emit(self, event: EventEnvelope) -> None:
        self._run_write(
            operation_name="emit",
            operation=lambda: self._conn.execute(
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
            ),
        )

    async def emit_async(self, event: EventEnvelope) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
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
            await cursor.close()

        await self._run_async_write(
            operation_name="emit_async",
            operation=lambda _conn: operation(),
        )

    def emit_run_event(self, event: RunEvent) -> int:
        lastrowid = self._run_write(
            operation_name="emit_run_event",
            operation=lambda: (
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
                ).lastrowid
            ),
        )
        if lastrowid is None:
            raise RuntimeError("Failed to persist run event id")
        return int(lastrowid)

    async def emit_run_event_async(self, event: RunEvent) -> int:
        async def operation() -> int | None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
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
            inserted_row_id = cursor.lastrowid
            await cursor.close()
            return inserted_row_id

        lastrowid = await self._run_async_write(
            operation_name="emit_run_event_async",
            operation=lambda _conn: operation(),
        )
        if lastrowid is None:
            raise RuntimeError("Failed to persist run event id")
        return int(lastrowid)

    def list_by_trace(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? ORDER BY id ASC",
                (trace_id,),
            ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    async def list_by_trace_async(
        self, trace_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? ORDER BY id ASC",
                (trace_id,),
            )
        )
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_trace_after_id(
        self, trace_id: str, after_event_id: int
    ) -> tuple[dict[str, JsonValue], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? AND id>? ORDER BY id ASC",
                (trace_id, after_event_id),
            ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    async def list_by_trace_after_id_async(
        self, trace_id: str, after_event_id: int
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? AND id>? ORDER BY id ASC",
                (trace_id, after_event_id),
            )
        )
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_trace_with_ids(self, trace_id: str) -> tuple[dict[str, JsonValue], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? ORDER BY id ASC",
                (trace_id,),
            ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    async def list_by_trace_with_ids_async(
        self, trace_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE trace_id=? ORDER BY id ASC",
                (trace_id,),
            )
        )
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[dict[str, JsonValue], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    async def list_by_session_async(
        self, session_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            )
        )
        return tuple(self._row_to_dict(row) for row in rows)

    def list_by_session_with_ids(
        self, session_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return tuple(self._row_to_dict(row) for row in rows)

    async def list_by_session_with_ids_async(
        self, session_id: str
    ) -> tuple[dict[str, JsonValue], ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            )
        )
        return tuple(self._row_to_dict(row) for row in rows)

    def list_run_states(self) -> tuple[RunStateRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events ORDER BY id ASC"
            ).fetchall()
        return self._run_states_from_rows(rows)

    async def list_run_states_async(self) -> tuple[RunStateRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT id, event_type, trace_id, session_id, task_id, instance_id, payload_json, occurred_at "
                "FROM events ORDER BY id ASC",
            )
        )
        return self._run_states_from_rows(rows)

    def _run_states_from_rows(
        self,
        rows: list[sqlite3.Row] | tuple[sqlite3.Row, ...],
    ) -> tuple[RunStateRecord, ...]:
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

    async def get_run_state_async(self, run_id: str) -> RunStateRecord | None:
        state: RunStateRecord | None = None
        for row in await self.list_by_trace_with_ids_async(run_id):
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
        self._run_write(
            operation_name="delete_by_session",
            operation=lambda: self._conn.execute(
                "DELETE FROM events WHERE session_id=?", (session_id,)
            ),
        )

    async def delete_by_session_async(self, session_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM events WHERE session_id=?", (session_id,)
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_session_async",
            operation=lambda _conn: operation(),
        )

    def delete_by_trace(self, trace_id: str) -> None:
        self._run_write(
            operation_name="delete_by_trace",
            operation=lambda: self._conn.execute(
                "DELETE FROM events WHERE trace_id=?",
                (trace_id,),
            ),
        )

    async def delete_by_trace_async(self, trace_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM events WHERE trace_id=?",
                (trace_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_trace_async",
            operation=lambda _conn: operation(),
        )

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
