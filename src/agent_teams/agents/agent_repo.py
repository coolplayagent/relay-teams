# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.agents.enums import InstanceStatus
from agent_teams.agents.models import AgentRuntimeRecord
from agent_teams.persistence.db import open_sqlite
from agent_teams.workspace import build_conversation_id


class AgentInstanceRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_instances (
                run_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                instance_id TEXT PRIMARY KEY,
                role_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '',
                conversation_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        columns = [
            str(row["name"])
            for row in self._conn.execute(
                "PRAGMA table_info(agent_instances)"
            ).fetchall()
        ]
        if "workspace_id" not in columns:
            self._conn.execute(
                "ALTER TABLE agent_instances ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''"
            )
        if "conversation_id" not in columns:
            self._conn.execute(
                "ALTER TABLE agent_instances ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''"
            )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status ON agent_instances(run_id, status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_agent_instances_session_role ON agent_instances(session_id, role_id, updated_at)"
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
        workspace_id: str,
        conversation_id: str | None = None,
        status: InstanceStatus,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        resolved_conversation_id = conversation_id or build_conversation_id(
            session_id,
            role_id,
        )
        self._conn.execute(
            """
            INSERT INTO agent_instances(run_id, trace_id, session_id, instance_id, role_id, workspace_id, conversation_id, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instance_id)
            DO UPDATE SET
                run_id=excluded.run_id,
                trace_id=excluded.trace_id,
                session_id=excluded.session_id,
                role_id=excluded.role_id,
                status=excluded.status,
                workspace_id=excluded.workspace_id,
                conversation_id=excluded.conversation_id,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                trace_id,
                session_id,
                instance_id,
                role_id,
                workspace_id,
                resolved_conversation_id,
                status.value,
                now,
                now,
            ),
        )
        self._conn.commit()

    def mark_status(self, instance_id: str, status: InstanceStatus) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE agent_instances SET status=?, updated_at=? WHERE instance_id=?",
            (status.value, now, instance_id),
        )
        self._conn.commit()

    def mark_running_instances_failed(self) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT instance_id FROM agent_instances WHERE status=? ORDER BY created_at ASC",
            (InstanceStatus.RUNNING.value,),
        ).fetchall()
        instance_ids = tuple(str(row["instance_id"]) for row in rows)
        if not instance_ids:
            return ()
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE agent_instances SET status=?, updated_at=? WHERE status=?",
            (InstanceStatus.FAILED.value, now, InstanceStatus.RUNNING.value),
        )
        self._conn.commit()
        return instance_ids

    def list_all(self) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM agent_instances ORDER BY created_at ASC",
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_running(self, run_id: str) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM agent_instances WHERE run_id=? AND status=? ORDER BY created_at ASC",
            (run_id, InstanceStatus.RUNNING.value),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_run(self, run_id: str) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM agent_instances WHERE run_id=? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM agent_instances WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_session_role_instances(
        self, session_id: str
    ) -> tuple[AgentRuntimeRecord, ...]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM agent_instances
            WHERE session_id=?
            ORDER BY role_id ASC, updated_at DESC, created_at DESC
            """,
            (session_id,),
        ).fetchall()
        latest_by_role: dict[str, AgentRuntimeRecord] = {}
        for row in rows:
            record = self._to_record(row)
            latest_by_role.setdefault(record.role_id, record)
        return tuple(
            latest_by_role[role_id] for role_id in sorted(latest_by_role.keys())
        )

    def get_instance(self, instance_id: str) -> AgentRuntimeRecord:
        row = self._conn.execute(
            "SELECT * FROM agent_instances WHERE instance_id=?",
            (instance_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown instance_id: {instance_id}")
        return self._to_record(row)

    def get_session_role_instance(
        self, session_id: str, role_id: str
    ) -> AgentRuntimeRecord | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM agent_instances
            WHERE session_id=? AND role_id=?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id, role_id),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def get_session_role_instance_id(self, session_id: str, role_id: str) -> str | None:
        record = self.get_session_role_instance(session_id, role_id)
        return record.instance_id if record is not None else None

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM agent_instances WHERE session_id=?", (session_id,)
        )
        self._conn.commit()

    def _to_record(self, row: sqlite3.Row) -> AgentRuntimeRecord:
        return AgentRuntimeRecord(
            run_id=str(row["run_id"]),
            trace_id=str(row["trace_id"]),
            session_id=str(row["session_id"]),
            instance_id=str(row["instance_id"]),
            role_id=str(row["role_id"]),
            workspace_id=str(row["workspace_id"]),
            conversation_id=str(
                row["conversation_id"]
                or build_conversation_id(
                    str(row["session_id"]),
                    str(row["role_id"]),
                )
            ),
            status=InstanceStatus(str(row["status"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
