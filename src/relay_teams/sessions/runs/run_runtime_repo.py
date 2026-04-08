from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.validation import (
    OptionalIdentifierStr,
    RequiredIdentifierStr,
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class RunRuntimeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"
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
    AWAITING_RECOVERY = "awaiting_recovery"
    MANUAL = "manual"
    TERMINAL = "terminal"


class RunRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    root_task_id: OptionalIdentifierStr = None
    status: RunRuntimeStatus = RunRuntimeStatus.QUEUED
    phase: RunRuntimePhase = RunRuntimePhase.IDLE
    active_instance_id: OptionalIdentifierStr = None
    active_task_id: OptionalIdentifierStr = None
    active_role_id: OptionalIdentifierStr = None
    active_subagent_instance_id: OptionalIdentifierStr = None
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
            return self._record_or_none(row, fallback_invalid_timestamps=True)

    def list_by_session(self, session_id: str) -> tuple[RunRuntimeRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM run_runtime WHERE session_id=? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
            return tuple(
                record
                for row in rows
                if (record := self._record_or_none(row)) is not None
            )

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
            return tuple(
                record
                for row in rows
                if (record := self._record_or_none(row)) is not None
            )

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

    def _to_record(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> RunRuntimeRecord:
        run_id = require_persisted_identifier(row["run_id"], field_name="run_id")
        created_at, updated_at = _load_runtime_timestamps(
            row=row,
            run_id=run_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        return RunRuntimeRecord(
            run_id=run_id,
            session_id=require_persisted_identifier(
                row["session_id"],
                field_name="session_id",
            ),
            root_task_id=normalize_persisted_text(row["root_task_id"]),
            status=RunRuntimeStatus(str(row["status"])),
            phase=RunRuntimePhase(str(row["phase"])),
            active_instance_id=normalize_persisted_text(row["active_instance_id"]),
            active_task_id=normalize_persisted_text(row["active_task_id"]),
            active_role_id=normalize_persisted_text(row["active_role_id"]),
            active_subagent_instance_id=normalize_persisted_text(
                row["active_subagent_instance_id"]
            ),
            last_error=str(row["last_error"]) if row["last_error"] else None,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _record_or_none(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> RunRuntimeRecord | None:
        try:
            return self._to_record(
                row,
                fallback_invalid_timestamps=fallback_invalid_timestamps,
            )
        except (ValidationError, ValueError) as exc:
            _log_invalid_runtime_row(row=row, error=exc)
            return None


def _load_runtime_timestamps(
    *,
    row: sqlite3.Row,
    run_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            _log_invalid_runtime_timestamp(
                run_id=run_id,
                field_name="created_at",
                raw_preview=_persisted_value_preview(row["created_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            _log_invalid_runtime_timestamp(
                run_id=run_id,
                field_name="updated_at",
                raw_preview=_persisted_value_preview(row["updated_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted updated_at")
        return created_at, updated_at
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_runtime_timestamp(
            run_id=run_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_runtime_timestamp(
            run_id=run_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
            fallback_iso=updated_at.isoformat(),
        )
    return created_at, updated_at


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_runtime_timestamp(
    *,
    run_id: str,
    field_name: str,
    raw_preview: str,
    fallback_iso: str | None,
) -> None:
    payload: dict[str, JsonValue] = {
        "run_id": run_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.run_runtime_repo.timestamp_invalid",
        message=(
            "Using fallback for invalid persisted run runtime timestamp"
            if fallback_iso is not None
            else "Invalid persisted run runtime timestamp"
        ),
        payload=payload,
    )


def _log_invalid_runtime_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "run_id": _persisted_value_preview(row["run_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.run_runtime_repo.row_invalid",
        message="Skipping invalid persisted run runtime row",
        payload=payload,
    )
