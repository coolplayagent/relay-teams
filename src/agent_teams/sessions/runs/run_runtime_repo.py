from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry


class RunRuntimeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class RunRuntimePhase(str, Enum):
    IDLE = "idle"
    COORDINATOR_RUNNING = "coordinator_running"
    SUBAGENT_RUNNING = "subagent_running"
    AWAITING_TOOL_APPROVAL = "awaiting_tool_approval"
    AWAITING_SUBAGENT_FOLLOWUP = "awaiting_subagent_followup"
    MANUAL = "manual"
    TERMINAL = "terminal"


class RunRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    root_task_id: str | None = None
    status: RunRuntimeStatus = RunRuntimeStatus.QUEUED
    phase: RunRuntimePhase = RunRuntimePhase.IDLE
    active_instance_id: str | None = None
    active_task_id: str | None = None
    active_role_id: str | None = None
    active_subagent_instance_id: str | None = None
    last_error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def is_recoverable(self) -> bool:
        return self.status in {
            RunRuntimeStatus.QUEUED,
            RunRuntimeStatus.RUNNING,
            RunRuntimeStatus.PAUSED,
            RunRuntimeStatus.STOPPED,
        }


class RunRuntimeRepository:
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
                CREATE TABLE IF NOT EXISTS run_runtime (
                    run_id                     TEXT PRIMARY KEY,
                    session_id                 TEXT NOT NULL,
                    root_task_id               TEXT,
                    status                     TEXT NOT NULL,
                    phase                      TEXT NOT NULL,
                    active_instance_id         TEXT,
                    active_task_id             TEXT,
                    active_role_id             TEXT,
                    active_subagent_instance_id TEXT,
                    last_error                 TEXT,
                    created_at                 TEXT NOT NULL,
                    updated_at                 TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_runtime_session_updated ON run_runtime(session_id, updated_at DESC)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_run_runtime_status ON run_runtime(status, updated_at DESC)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="RunRuntimeRepository",
            operation_name="init_tables",
        )

    def upsert(self, record: RunRuntimeRecord) -> RunRuntimeRecord:
        def operation() -> None:
            existing = self.get(record.run_id)
            created_at = (
                existing.created_at.isoformat()
                if existing is not None
                else record.created_at.isoformat()
            )
            updated_at = record.updated_at.isoformat()
            self._conn.execute(
                """
                INSERT INTO run_runtime(run_id, session_id, root_task_id, status, phase, active_instance_id,
                                        active_task_id, active_role_id, active_subagent_instance_id,
                                        last_error, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id)
                DO UPDATE SET
                    session_id=excluded.session_id,
                    root_task_id=excluded.root_task_id,
                    status=excluded.status,
                    phase=excluded.phase,
                    active_instance_id=excluded.active_instance_id,
                    active_task_id=excluded.active_task_id,
                    active_role_id=excluded.active_role_id,
                    active_subagent_instance_id=excluded.active_subagent_instance_id,
                    last_error=excluded.last_error,
                    updated_at=excluded.updated_at
                """,
                (
                    record.run_id,
                    record.session_id,
                    record.root_task_id,
                    record.status.value,
                    record.phase.value,
                    record.active_instance_id,
                    record.active_task_id,
                    record.active_role_id,
                    record.active_subagent_instance_id,
                    record.last_error,
                    created_at,
                    updated_at,
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="RunRuntimeRepository",
            operation_name="upsert",
        )
        next_record = self.get(record.run_id)
        if next_record is None:
            raise RuntimeError(f"Failed to persist run runtime {record.run_id}")
        return next_record

    def ensure(
        self,
        *,
        run_id: str,
        session_id: str,
        root_task_id: str | None = None,
        status: RunRuntimeStatus = RunRuntimeStatus.QUEUED,
        phase: RunRuntimePhase = RunRuntimePhase.IDLE,
    ) -> RunRuntimeRecord:
        existing = self.get(run_id)
        if existing is not None:
            update = {}
            if root_task_id and not existing.root_task_id:
                update["root_task_id"] = root_task_id
            if update:
                return self.update(run_id, **update)
            return existing
        return self.upsert(
            RunRuntimeRecord(
                run_id=run_id,
                session_id=session_id,
                root_task_id=root_task_id,
                status=status,
                phase=phase,
            )
        )

    def update(self, run_id: str, **changes: object) -> RunRuntimeRecord:
        current = self.get(run_id)
        if current is None:
            raise KeyError(f"Unknown run_id: {run_id}")
        update = dict(changes)
        update["updated_at"] = datetime.now(tz=timezone.utc)
        next_record = current.model_copy(update=update)
        return self.upsert(next_record)

    def get(self, run_id: str) -> RunRuntimeRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM run_runtime WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return self._to_record(row)

    def list_by_session(self, session_id: str) -> tuple[RunRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM run_runtime WHERE session_id=? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
            return tuple(self._to_record(row) for row in rows)

    def list_recoverable(self) -> tuple[RunRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM run_runtime
                WHERE status IN (?, ?, ?, ?)
                ORDER BY updated_at DESC
                """,
                (
                    RunRuntimeStatus.QUEUED.value,
                    RunRuntimeStatus.RUNNING.value,
                    RunRuntimeStatus.PAUSED.value,
                    RunRuntimeStatus.STOPPED.value,
                ),
            ).fetchall()
            return tuple(self._to_record(row) for row in rows)

    def mark_transient_runs_interrupted(self) -> int:
        affected = 0

        def operation() -> None:
            nonlocal affected
            updated_at = datetime.now(tz=timezone.utc).isoformat()
            cursor = self._conn.execute(
                """
                UPDATE run_runtime
                SET
                    status=?,
                    phase=?,
                    active_instance_id=NULL,
                    active_task_id=NULL,
                    active_role_id=NULL,
                    active_subagent_instance_id=NULL,
                    last_error=?,
                    updated_at=?
                WHERE status IN (?, ?)
                """,
                (
                    RunRuntimeStatus.STOPPED.value,
                    RunRuntimePhase.IDLE.value,
                    "interrupted_by_process_restart",
                    updated_at,
                    RunRuntimeStatus.QUEUED.value,
                    RunRuntimeStatus.RUNNING.value,
                ),
            )
            affected = int(cursor.rowcount or 0)

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="RunRuntimeRepository",
            operation_name="mark_transient_runs_interrupted",
        )
        return affected

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM run_runtime WHERE session_id=?", (session_id,)
            ),
            lock=self._lock,
            repository_name="RunRuntimeRepository",
            operation_name="delete_by_session",
        )

    def _to_record(self, row: sqlite3.Row) -> RunRuntimeRecord:
        return RunRuntimeRecord(
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            root_task_id=str(row["root_task_id"]) if row["root_task_id"] else None,
            status=RunRuntimeStatus(str(row["status"])),
            phase=RunRuntimePhase(str(row["phase"])),
            active_instance_id=(
                str(row["active_instance_id"]) if row["active_instance_id"] else None
            ),
            active_task_id=str(row["active_task_id"])
            if row["active_task_id"]
            else None,
            active_role_id=str(row["active_role_id"])
            if row["active_role_id"]
            else None,
            active_subagent_instance_id=(
                str(row["active_subagent_instance_id"])
                if row["active_subagent_instance_id"]
                else None
            ),
            last_error=str(row["last_error"]) if row["last_error"] else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
