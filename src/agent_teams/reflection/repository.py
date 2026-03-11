# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.reflection.models import (
    MemoryOwnerScope,
    ReflectionJobCreate,
    ReflectionJobRecord,
    ReflectionJobStatus,
    ReflectionJobType,
)
from agent_teams.state.db import open_sqlite


class ReflectionJobRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reflection_jobs (
                    job_id              TEXT PRIMARY KEY,
                    job_type            TEXT NOT NULL,
                    session_id          TEXT NOT NULL,
                    run_id              TEXT NOT NULL,
                    task_id             TEXT NOT NULL,
                    instance_id         TEXT NOT NULL,
                    role_id             TEXT NOT NULL,
                    workspace_id        TEXT NOT NULL,
                    conversation_id     TEXT NOT NULL,
                    memory_owner_scope  TEXT NOT NULL,
                    memory_owner_id     TEXT NOT NULL,
                    trigger_date        TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    attempt_count       INTEGER NOT NULL,
                    last_error          TEXT,
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflection_jobs_status_created "
                "ON reflection_jobs(status, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflection_jobs_session "
                "ON reflection_jobs(session_id, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reflection_jobs_owner_type_date "
                "ON reflection_jobs(memory_owner_id, job_type, trigger_date)"
            )
            self._conn.commit()

    def enqueue(self, payload: ReflectionJobCreate) -> ReflectionJobRecord:
        now = datetime.now(tz=timezone.utc)
        record = ReflectionJobRecord(
            job_id=f"rjob-{uuid.uuid4().hex[:12]}",
            job_type=payload.job_type,
            session_id=payload.session_id,
            run_id=payload.run_id,
            task_id=payload.task_id,
            instance_id=payload.instance_id,
            role_id=payload.role_id,
            workspace_id=payload.workspace_id,
            conversation_id=payload.conversation_id,
            memory_owner_scope=payload.memory_owner_scope,
            memory_owner_id=payload.memory_owner_id,
            trigger_date=payload.trigger_date,
            status=ReflectionJobStatus.QUEUED,
            attempt_count=0,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    """
                    INSERT INTO reflection_jobs(
                        job_id, job_type, session_id, run_id, task_id, instance_id,
                        role_id, workspace_id, conversation_id, memory_owner_scope,
                        memory_owner_id, trigger_date, status, attempt_count,
                        last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.job_id,
                        record.job_type.value,
                        record.session_id,
                        record.run_id,
                        record.task_id,
                        record.instance_id,
                        record.role_id,
                        record.workspace_id,
                        record.conversation_id,
                        record.memory_owner_scope.value,
                        record.memory_owner_id,
                        record.trigger_date,
                        record.status.value,
                        record.attempt_count,
                        record.last_error,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
            )
            self._run_write_with_retry(self._conn.commit)
        return record

    def claim_next_job(self, *, max_retry_attempts: int) -> ReflectionJobRecord | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM reflection_jobs
                WHERE status=? AND attempt_count < ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (ReflectionJobStatus.QUEUED.value, max_retry_attempts),
            ).fetchone()
            if row is None:
                return None
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    """
                    UPDATE reflection_jobs
                    SET status=?, attempt_count=attempt_count + 1, updated_at=?
                    WHERE job_id=? AND status=?
                    """,
                    (
                        ReflectionJobStatus.RUNNING.value,
                        datetime.now(tz=timezone.utc).isoformat(),
                        str(row["job_id"]),
                        ReflectionJobStatus.QUEUED.value,
                    ),
                )
            )
            self._run_write_with_retry(self._conn.commit)
            updated = self._conn.execute(
                "SELECT * FROM reflection_jobs WHERE job_id=?",
                (str(row["job_id"]),),
            ).fetchone()
        return self._to_record(updated) if updated is not None else None

    def mark_completed(self, job_id: str) -> None:
        self._mark_terminal(
            job_id=job_id,
            status=ReflectionJobStatus.COMPLETED,
            last_error=None,
        )

    def mark_failed(self, job_id: str, *, last_error: str) -> None:
        self._mark_terminal(
            job_id=job_id,
            status=ReflectionJobStatus.FAILED,
            last_error=last_error,
        )

    def get(self, job_id: str) -> ReflectionJobRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM reflection_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown reflection job: {job_id}")
        return self._to_record(row)

    def list_jobs(self, *, limit: int = 50) -> tuple[ReflectionJobRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM reflection_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def retry(self, job_id: str) -> ReflectionJobRecord:
        with self._lock:
            current = self._conn.execute(
                "SELECT * FROM reflection_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if current is None:
                raise KeyError(f"Unknown reflection job: {job_id}")
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    """
                    UPDATE reflection_jobs
                    SET status=?, last_error=NULL, updated_at=?
                    WHERE job_id=?
                    """,
                    (
                        ReflectionJobStatus.QUEUED.value,
                        datetime.now(tz=timezone.utc).isoformat(),
                        job_id,
                    ),
                )
            )
            self._run_write_with_retry(self._conn.commit)
            updated = self._conn.execute(
                "SELECT * FROM reflection_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if updated is None:
            raise KeyError(f"Unknown reflection job: {job_id}")
        return self._to_record(updated)

    def reset_running_to_queued(self) -> None:
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    """
                    UPDATE reflection_jobs
                    SET status=?, updated_at=?
                    WHERE status=?
                    """,
                    (
                        ReflectionJobStatus.QUEUED.value,
                        datetime.now(tz=timezone.utc).isoformat(),
                        ReflectionJobStatus.RUNNING.value,
                    ),
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def delete_by_session(self, session_id: str) -> None:
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    "DELETE FROM reflection_jobs WHERE session_id=?",
                    (session_id,),
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def has_job_for_owner_date(
        self,
        *,
        job_type: ReflectionJobType,
        memory_owner_id: str,
        trigger_date: str,
    ) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM reflection_jobs
                WHERE job_type=?
                  AND memory_owner_id=?
                  AND trigger_date=?
                  AND status IN (?, ?, ?)
                LIMIT 1
                """,
                (
                    job_type.value,
                    memory_owner_id,
                    trigger_date,
                    ReflectionJobStatus.QUEUED.value,
                    ReflectionJobStatus.RUNNING.value,
                    ReflectionJobStatus.COMPLETED.value,
                ),
            ).fetchone()
        return row is not None

    def _mark_terminal(
        self,
        *,
        job_id: str,
        status: ReflectionJobStatus,
        last_error: str | None,
    ) -> None:
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    """
                    UPDATE reflection_jobs
                    SET status=?, last_error=?, updated_at=?
                    WHERE job_id=?
                    """,
                    (
                        status.value,
                        last_error,
                        datetime.now(tz=timezone.utc).isoformat(),
                        job_id,
                    ),
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def _to_record(self, row: sqlite3.Row) -> ReflectionJobRecord:
        return ReflectionJobRecord(
            job_id=str(row["job_id"]),
            job_type=ReflectionJobType(str(row["job_type"])),
            session_id=str(row["session_id"]),
            run_id=str(row["run_id"]),
            task_id=str(row["task_id"]),
            instance_id=str(row["instance_id"]),
            role_id=str(row["role_id"]),
            workspace_id=str(row["workspace_id"]),
            conversation_id=str(row["conversation_id"]),
            memory_owner_scope=MemoryOwnerScope(str(row["memory_owner_scope"])),
            memory_owner_id=str(row["memory_owner_id"]),
            trigger_date=str(row["trigger_date"]),
            status=ReflectionJobStatus(str(row["status"])),
            attempt_count=int(row["attempt_count"]),
            last_error=str(row["last_error"]) if row["last_error"] else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def _run_write_with_retry(self, op: Callable[[], object]) -> None:
        max_retries = 8
        delay = 0.01
        for attempt in range(max_retries + 1):
            try:
                _ = op()
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                retryable = (
                    "database is locked" in message
                    or "database table is locked" in message
                    or "another row available" in message
                )
                if not retryable or attempt >= max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.2)
