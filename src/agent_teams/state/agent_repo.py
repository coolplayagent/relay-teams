from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.core.enums import InstanceStatus
from agent_teams.core.models import AgentRuntimeRecord
from agent_teams.state.db import open_sqlite


class AgentInstanceRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_instances (
                run_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                instance_id TEXT PRIMARY KEY,
                role_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status ON agent_instances(run_id, status)'
        )
        self._conn.commit()

    def upsert_instance(
        self,
        *,
        run_id: str,
        trace_id: str,
        session_id: str,
        instance_id: str,
        role_id: str,
        status: InstanceStatus,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            '''
            INSERT INTO agent_instances(run_id, trace_id, session_id, instance_id, role_id, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id)
            DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at
            ''',
            (run_id, trace_id, session_id, instance_id, role_id, status.value, now, now),
        )
        self._conn.commit()

    def mark_status(self, instance_id: str, status: InstanceStatus) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            'UPDATE agent_instances SET status=?, updated_at=? WHERE instance_id=?',
            (status.value, now, instance_id),
        )
        self._conn.commit()

    def list_all(self) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            'SELECT * FROM agent_instances ORDER BY created_at ASC',
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_running(self, run_id: str) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            'SELECT * FROM agent_instances WHERE run_id=? AND status=? ORDER BY created_at ASC',
            (run_id, InstanceStatus.RUNNING.value),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            'SELECT * FROM agent_instances WHERE session_id=? ORDER BY created_at ASC',
            (session_id,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute('DELETE FROM agent_instances WHERE session_id=?', (session_id,))
        self._conn.commit()

    def _to_record(self, row: sqlite3.Row) -> AgentRuntimeRecord:
        return AgentRuntimeRecord(
            run_id=str(row['run_id']),
            trace_id=str(row['trace_id']),
            session_id=str(row['session_id']),
            instance_id=str(row['instance_id']),
            role_id=str(row['role_id']),
            status=InstanceStatus(str(row['status'])),
            created_at=datetime.fromisoformat(str(row['created_at'])),
            updated_at=datetime.fromisoformat(str(row['updated_at'])),
        )
