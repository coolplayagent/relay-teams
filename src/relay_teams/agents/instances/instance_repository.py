# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Optional

from relay_teams.agents.instances.enums import InstanceLifecycle, InstanceStatus
from relay_teams.agents.instances.models import AgentRuntimeRecord
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.workspace import build_conversation_id

_SQLITE_SAFE_VARIABLE_LIMIT = 900


class AgentInstanceRepository:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
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
                    lifecycle TEXT NOT NULL DEFAULT 'reusable',
                    parent_instance_id TEXT,
                    runtime_system_prompt TEXT NOT NULL DEFAULT '',
                    runtime_tools_json TEXT NOT NULL DEFAULT '',
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
            if "runtime_system_prompt" not in columns:
                self._conn.execute(
                    "ALTER TABLE agent_instances ADD COLUMN runtime_system_prompt TEXT NOT NULL DEFAULT ''"
                )
            if "runtime_tools_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE agent_instances ADD COLUMN runtime_tools_json TEXT NOT NULL DEFAULT ''"
                )
            if "lifecycle" not in columns:
                self._conn.execute(
                    "ALTER TABLE agent_instances ADD COLUMN lifecycle TEXT NOT NULL DEFAULT 'reusable'"
                )
            if "parent_instance_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE agent_instances ADD COLUMN parent_instance_id TEXT"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_instances_run_status ON agent_instances(run_id, status)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_instances_session_role ON agent_instances(session_id, role_id, updated_at)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="init_tables",
        )

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
        lifecycle: Optional[InstanceLifecycle] = None,
        parent_instance_id: Optional[str] = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        resolved_conversation_id = conversation_id or build_conversation_id(
            session_id,
            role_id,
        )
        lifecycle_value = lifecycle.value if lifecycle is not None else None
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO agent_instances(run_id, trace_id, session_id, instance_id, role_id, workspace_id, conversation_id, status, lifecycle, parent_instance_id, runtime_system_prompt, runtime_tools_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 'reusable'), ?, '', '', ?, ?)
                ON CONFLICT(instance_id)
                DO UPDATE SET
                    run_id=excluded.run_id,
                    trace_id=excluded.trace_id,
                    session_id=excluded.session_id,
                    role_id=excluded.role_id,
                    status=excluded.status,
                    workspace_id=excluded.workspace_id,
                    conversation_id=excluded.conversation_id,
                    lifecycle=CASE
                        WHEN ? IS NULL THEN agent_instances.lifecycle
                        ELSE excluded.lifecycle
                    END,
                    parent_instance_id=CASE
                        WHEN ? IS NULL THEN agent_instances.parent_instance_id
                        ELSE excluded.parent_instance_id
                    END,
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
                    lifecycle_value,
                    parent_instance_id,
                    now,
                    now,
                    lifecycle_value,
                    lifecycle_value,
                ),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="upsert_instance",
        )

    def update_runtime_snapshot(
        self,
        instance_id: str,
        *,
        runtime_system_prompt: str,
        runtime_tools_json: str,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE agent_instances
                SET runtime_system_prompt=?, runtime_tools_json=?, updated_at=?
                WHERE instance_id=?
                """,
                (runtime_system_prompt, runtime_tools_json, now, instance_id),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="update_runtime_snapshot",
        )

    def mark_status(self, instance_id: str, status: InstanceStatus) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "UPDATE agent_instances SET status=?, updated_at=? WHERE instance_id=?",
                (status.value, now, instance_id),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="mark_status",
        )

    def update_session_workspace(self, session_id: str, *, workspace_id: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE agent_instances
                SET workspace_id=?, updated_at=?
                WHERE session_id=?
                """,
                (workspace_id, now, session_id),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="update_session_workspace",
        )

    def mark_running_instances_failed(self) -> tuple[str, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT instance_id FROM agent_instances WHERE status=? ORDER BY created_at ASC",
                (InstanceStatus.RUNNING.value,),
            ).fetchall()
            instance_ids = tuple(str(row["instance_id"]) for row in rows)
        if not instance_ids:
            return ()
        now = datetime.now(tz=timezone.utc).isoformat()
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "UPDATE agent_instances SET status=?, updated_at=? WHERE status=?",
                (InstanceStatus.FAILED.value, now, InstanceStatus.RUNNING.value),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="mark_running_instances_failed",
        )
        return instance_ids

    def list_all(self) -> tuple[AgentRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_instances ORDER BY created_at ASC",
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_running(self, run_id: str) -> tuple[AgentRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_instances WHERE run_id=? AND status=? ORDER BY created_at ASC",
                (run_id, InstanceStatus.RUNNING.value),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_run(self, run_id: str) -> tuple[AgentRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_instances WHERE run_id=? ORDER BY created_at ASC",
                (run_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[AgentRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_instances WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def count_normal_mode_subagents_by_session_ids(
        self,
        session_ids: tuple[str, ...],
    ) -> dict[str, int]:
        if len(session_ids) == 0:
            return {}
        subagent_counts: dict[str, int] = {}
        with self._lock:
            for index in range(0, len(session_ids), _SQLITE_SAFE_VARIABLE_LIMIT):
                session_id_chunk = session_ids[
                    index : index + _SQLITE_SAFE_VARIABLE_LIMIT
                ]
                placeholders = ", ".join("?" for _ in session_id_chunk)
                rows = self._conn.execute(
                    f"""
                    SELECT session_id, COUNT(*) AS subagent_count
                    FROM agent_instances
                    WHERE session_id IN ({placeholders})
                      AND run_id GLOB 'subagent_run_*'
                    GROUP BY session_id
                    """,
                    session_id_chunk,
                ).fetchall()
                for row in rows:
                    subagent_counts[str(row["session_id"])] = int(row["subagent_count"])
        return subagent_counts

    def list_session_role_instances(
        self, session_id: str
    ) -> tuple[AgentRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM agent_instances
                WHERE session_id=? AND lifecycle=?
                ORDER BY role_id ASC, updated_at DESC, created_at DESC
                """,
                (session_id, InstanceLifecycle.REUSABLE.value),
            ).fetchall()
        latest_by_role: dict[str, AgentRuntimeRecord] = {}
        for row in rows:
            record = self._to_record(row)
            latest_by_role.setdefault(record.role_id, record)
        return tuple(
            latest_by_role[role_id] for role_id in sorted(latest_by_role.keys())
        )

    def get_instance(self, instance_id: str) -> AgentRuntimeRecord:
        with self._lock:
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
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM agent_instances
                WHERE session_id=? AND role_id=? AND lifecycle=?
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
                (session_id, role_id, InstanceLifecycle.REUSABLE.value),
            ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def get_session_role_instance_id(self, session_id: str, role_id: str) -> str | None:
        record = self.get_session_role_instance(session_id, role_id)
        return record.instance_id if record is not None else None

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM agent_instances WHERE session_id=?", (session_id,)
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="delete_by_session",
        )

    def delete_instance(self, instance_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM agent_instances WHERE instance_id=?",
                (instance_id,),
            ),
            lock=self._lock,
            repository_name="AgentInstanceRepository",
            operation_name="delete_instance",
        )

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
            lifecycle=InstanceLifecycle(str(row["lifecycle"] or "reusable")),
            parent_instance_id=str(row["parent_instance_id"])
            if row["parent_instance_id"]
            else None,
            runtime_system_prompt=str(row["runtime_system_prompt"] or ""),
            runtime_tools_json=str(row["runtime_tools_json"] or ""),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
