from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent_teams.sessions.runs.enums import RunEventType
from agent_teams.sessions.runs.models import RunEvent
from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.runs.run_state_models import (
    RunSnapshotRecord,
    RunStateRecord,
    apply_run_event_to_state,
)

_SNAPSHOT_EVENT_TYPES = {
    RunEventType.RUN_STARTED,
    RunEventType.RUN_RESUMED,
    RunEventType.MODEL_STEP_STARTED,
    RunEventType.TOOL_APPROVAL_REQUESTED,
    RunEventType.TOOL_APPROVAL_RESOLVED,
    RunEventType.TOOL_RESULT,
    RunEventType.SUBAGENT_STOPPED,
    RunEventType.SUBAGENT_RESUMED,
    RunEventType.RUN_STOPPED,
    RunEventType.RUN_COMPLETED,
    RunEventType.RUN_FAILED,
}


class RunStateRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_states (
                run_id      TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                state_json  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_states_session ON run_states(session_id, updated_at DESC)"
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS run_snapshots (
                run_id               TEXT NOT NULL,
                session_id           TEXT NOT NULL,
                checkpoint_event_id  INTEGER NOT NULL,
                state_json           TEXT NOT NULL,
                created_at           TEXT NOT NULL,
                PRIMARY KEY (run_id, checkpoint_event_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_run_snapshots_session ON run_snapshots(session_id, created_at DESC)"
        )
        self._conn.commit()

    def apply_event(self, *, event_id: int, event: RunEvent) -> RunStateRecord:
        previous = self.get_run_state(event.run_id)
        next_state = apply_run_event_to_state(previous, event=event, event_id=event_id)
        self.upsert(next_state)
        if event.event_type in _SNAPSHOT_EVENT_TYPES:
            self._upsert_snapshot(
                RunSnapshotRecord(
                    run_id=next_state.run_id,
                    session_id=next_state.session_id,
                    checkpoint_event_id=next_state.checkpoint_event_id,
                    state=next_state,
                    created_at=next_state.updated_at,
                )
            )
        return next_state

    def upsert(self, state: RunStateRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO run_states(run_id, session_id, state_json, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(run_id)
            DO UPDATE SET
                session_id=excluded.session_id,
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            (
                state.run_id,
                state.session_id,
                state.model_dump_json(),
                state.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    def get_run_state(self, run_id: str) -> RunStateRecord | None:
        row = self._conn.execute(
            "SELECT state_json FROM run_states WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunStateRecord.model_validate_json(str(row["state_json"]))

    def get_latest_snapshot(self, run_id: str) -> RunSnapshotRecord | None:
        row = self._conn.execute(
            """
            SELECT checkpoint_event_id, state_json, session_id, created_at
            FROM run_snapshots
            WHERE run_id=?
            ORDER BY checkpoint_event_id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunSnapshotRecord(
            run_id=run_id,
            session_id=str(row["session_id"]),
            checkpoint_event_id=int(row["checkpoint_event_id"]),
            state=RunStateRecord.model_validate_json(str(row["state_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def list_by_session(self, session_id: str) -> tuple[RunStateRecord, ...]:
        rows = self._conn.execute(
            """
            SELECT state_json
            FROM run_states
            WHERE session_id=?
            ORDER BY updated_at DESC
            """,
            (session_id,),
        ).fetchall()
        return tuple(
            RunStateRecord.model_validate_json(str(row["state_json"])) for row in rows
        )

    def list_recoverable(self) -> tuple[RunStateRecord, ...]:
        rows = self._conn.execute("SELECT state_json FROM run_states").fetchall()
        result: list[RunStateRecord] = []
        for row in rows:
            state = RunStateRecord.model_validate_json(str(row["state_json"]))
            if state.recoverable:
                result.append(state)
        result.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(result)

    def _upsert_snapshot(self, snapshot: RunSnapshotRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO run_snapshots(run_id, session_id, checkpoint_event_id, state_json, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(run_id, checkpoint_event_id)
            DO UPDATE SET
                state_json=excluded.state_json,
                created_at=excluded.created_at
            """,
            (
                snapshot.run_id,
                snapshot.session_id,
                snapshot.checkpoint_event_id,
                json.dumps(snapshot.state.model_dump(mode="json")),
                snapshot.created_at.isoformat(),
            ),
        )
        self._conn.commit()
