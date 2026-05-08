# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Awaitable, Callable
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock, Thread
from typing import Literal, TypeVar, cast

import aiosqlite

from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import (
    TaskArtifact,
    TaskArtifactEntry,
    TaskArtifactSummary,
    VerificationEvidenceBundle,
)
from relay_teams.persistence.db import (
    BlockingSqliteConnection,
    SQLITE_BUSY_TIMEOUT_MS,
    SQLITE_TIMEOUT_SECONDS,
    open_async_sqlite,
    run_async_sqlite_write_with_retry,
    run_sqlite_write_with_retry,
)
from relay_teams.persistence.sqlite_repository import async_fetchall, async_fetchone
from relay_teams.logger import get_logger, log_event
from pydantic import BaseModel, ConfigDict

TASK_ARTIFACT_SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_MS
TASK_ARTIFACT_WRITE_QUEUE_MAXSIZE = 2000
TASK_ARTIFACT_WRITE_LOCK_RETRY_ATTEMPTS = 5
LOGGER = get_logger(__name__)
ResultT = TypeVar("ResultT")

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS task_artifacts (
    task_id TEXT NOT NULL PRIMARY KEY,
    spec_artifact_id TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    evidence_bundle_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_artifact_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    phase TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    role_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    linked_evidence_ids TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_artifact_entries_task_id
    ON task_artifact_entries(task_id);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_phase
    ON task_artifact_entries(phase);
CREATE INDEX IF NOT EXISTS idx_artifact_entries_event_type
    ON task_artifact_entries(event_type);
"""


class TaskArtifactWriteMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enqueued: int = 0
    completed: int = 0
    dropped: int = 0
    failed: int = 0
    sqlite_lock_timeout_count: int = 0
    total_wait_ms: int = 0
    total_duration_ms: int = 0


class _TaskArtifactWriteJob(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["ensure", "append", "update_summary", "update_evidence_bundle"]
    task_id: str
    enqueued_monotonic: float
    spec_artifact_id: str = ""
    entry: TaskArtifactEntry | None = None
    summary: str = ""
    evidence_bundle: VerificationEvidenceBundle | None = None


class TaskArtifactRepository:  # pragma: no cover
    """Persist and query task artifacts and entries."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._lock = RLock()
        self._queue: Queue[_TaskArtifactWriteJob] = Queue(
            maxsize=TASK_ARTIFACT_WRITE_QUEUE_MAXSIZE
        )
        self._worker_stop = Event()
        self._metrics_lock = Lock()
        self._metrics = TaskArtifactWriteMetrics()
        self._worker: Thread | None = None
        self._worker_lock = Lock()
        self._init_tables()

    def enqueue_ensure_artifact(self, *, task_id: str, spec_artifact_id: str) -> bool:
        return self._enqueue(
            _TaskArtifactWriteJob(
                operation="ensure",
                task_id=task_id,
                spec_artifact_id=spec_artifact_id,
                enqueued_monotonic=time.perf_counter(),
            )
        )

    def enqueue_append_entry(self, *, task_id: str, entry: TaskArtifactEntry) -> bool:
        return self._enqueue(
            _TaskArtifactWriteJob(
                operation="append",
                task_id=task_id,
                entry=entry,
                enqueued_monotonic=time.perf_counter(),
            )
        )

    def enqueue_update_summary(self, *, task_id: str, summary: str) -> bool:
        return self._enqueue(
            _TaskArtifactWriteJob(
                operation="update_summary",
                task_id=task_id,
                summary=summary,
                enqueued_monotonic=time.perf_counter(),
            )
        )

    def enqueue_update_evidence_bundle(
        self,
        *,
        task_id: str,
        bundle: VerificationEvidenceBundle,
    ) -> bool:
        return self._enqueue(
            _TaskArtifactWriteJob(
                operation="update_evidence_bundle",
                task_id=task_id,
                evidence_bundle=bundle,
                enqueued_monotonic=time.perf_counter(),
            )
        )

    def write_metrics(self) -> TaskArtifactWriteMetrics:
        with self._metrics_lock:
            return self._metrics

    def drain_write_queue(self, *, timeout_seconds: float = 2.0) -> bool:
        """Wait briefly for best-effort queued writes to settle."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            metrics = self.write_metrics()
            if (
                metrics.completed + metrics.failed >= metrics.enqueued
                and self._queue.empty()
            ):
                return True
            time.sleep(0.01)
        metrics = self.write_metrics()
        return (
            metrics.completed + metrics.failed >= metrics.enqueued
            and self._queue.empty()
        )

    def _enqueue(self, job: _TaskArtifactWriteJob) -> bool:
        self._ensure_write_worker_started()
        try:
            self._queue.put_nowait(job)
        except Full:
            self._record_metrics(dropped=1)
            return False
        self._record_metrics(enqueued=1)
        return True

    def _ensure_write_worker_started(self) -> None:
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker_stop.clear()
            self._worker = Thread(
                target=self._run_write_worker,
                name="relay-teams-task-artifact-writer",
                daemon=True,
            )
            self._worker.start()

    def close(self) -> None:
        _ = self.drain_write_queue(timeout_seconds=1.0)
        self._worker_stop.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=1.0)

    def _run_write_worker(self) -> None:
        while not self._worker_stop.is_set():
            try:
                job = self._queue.get(timeout=0.25)
            except Empty:
                continue
            started = time.perf_counter()
            wait_ms = int((started - job.enqueued_monotonic) * 1000)
            try:
                self._execute_queued_job_with_retries(job)
                duration_ms = int((time.perf_counter() - started) * 1000)
                self._record_metrics(
                    completed=1,
                    total_wait_ms=wait_ms,
                    total_duration_ms=duration_ms,
                )
            except sqlite3.OperationalError as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                locked = "database is locked" in str(exc).lower()
                self._record_metrics(
                    failed=1,
                    sqlite_lock_timeout_count=1 if locked else 0,
                    total_wait_ms=wait_ms,
                    total_duration_ms=duration_ms,
                )
                self._log_queued_write_failure(job, exc)
            except Exception as exc:
                duration_ms = int((time.perf_counter() - started) * 1000)
                self._record_metrics(
                    failed=1,
                    total_wait_ms=wait_ms,
                    total_duration_ms=duration_ms,
                )
                self._log_queued_write_failure(job, exc)
            finally:
                self._queue.task_done()

    def _execute_queued_job_with_retries(self, job: _TaskArtifactWriteJob) -> None:
        retry_attempt = 0
        while True:
            try:
                self._execute_queued_job(job)
                return
            except sqlite3.OperationalError as exc:
                if (
                    "database is locked" not in str(exc).lower()
                    or retry_attempt >= TASK_ARTIFACT_WRITE_LOCK_RETRY_ATTEMPTS
                ):
                    raise
                retry_attempt += 1
                self._record_metrics(sqlite_lock_timeout_count=1)
                time.sleep(min(0.05 * (2**retry_attempt), 1.0))

    def _execute_queued_job(self, job: _TaskArtifactWriteJob) -> None:
        if job.operation == "ensure":
            self.ensure_artifact(
                task_id=job.task_id,
                spec_artifact_id=job.spec_artifact_id,
            )
            return
        if job.operation == "append":
            if job.entry is None:
                raise ValueError("Queued artifact append missing entry")
            self.append_entry(task_id=job.task_id, entry=job.entry)
            return
        if job.operation == "update_summary":
            self.update_summary(task_id=job.task_id, summary=job.summary)
            return
        if job.evidence_bundle is None:
            raise ValueError("Queued artifact evidence update missing bundle")
        self.update_evidence_bundle(task_id=job.task_id, bundle=job.evidence_bundle)

    def _record_metrics(
        self,
        *,
        enqueued: int = 0,
        completed: int = 0,
        dropped: int = 0,
        failed: int = 0,
        sqlite_lock_timeout_count: int = 0,
        total_wait_ms: int = 0,
        total_duration_ms: int = 0,
    ) -> None:
        with self._metrics_lock:
            self._metrics = TaskArtifactWriteMetrics(
                enqueued=self._metrics.enqueued + enqueued,
                completed=self._metrics.completed + completed,
                dropped=self._metrics.dropped + dropped,
                failed=self._metrics.failed + failed,
                sqlite_lock_timeout_count=(
                    self._metrics.sqlite_lock_timeout_count + sqlite_lock_timeout_count
                ),
                total_wait_ms=self._metrics.total_wait_ms + total_wait_ms,
                total_duration_ms=self._metrics.total_duration_ms + total_duration_ms,
            )

    @staticmethod
    def _log_queued_write_failure(
        job: _TaskArtifactWriteJob,
        exc: Exception,
    ) -> None:
        log_event(
            LOGGER,
            logging.WARNING,
            event="artifact.write_queue.failed",
            message="Queued artifact write failed",
            payload={
                "task_id": job.task_id,
                "operation": job.operation,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

    def _connect(self, *, enable_wal: bool = False) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=SQLITE_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA synchronous = NORMAL")
        if enable_wal:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError as exc:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    event="artifact.repository.wal_enable_failed",
                    message="Artifact repository could not enable WAL mode",
                    payload={
                        "db_path": str(self._db_path),
                        "error": str(exc),
                    },
                )
        return conn

    async def _connect_async(self) -> aiosqlite.Connection:
        conn = await open_async_sqlite(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        def operation(conn: sqlite3.Connection) -> None:
            conn.executescript(_CREATE_TABLES_SQL)

        self._run_write(operation_name="init_tables", operation=operation)

    def _run_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[sqlite3.Connection], ResultT],
    ) -> ResultT:
        conn = self._connect(enable_wal=True)
        try:
            return run_sqlite_write_with_retry(
                conn=cast(BlockingSqliteConnection, conn),
                db_path=self._db_path,
                operation=lambda: operation(conn),
                lock=self._lock,
                repository_name=type(self).__name__,
                operation_name=operation_name,
            )
        finally:
            conn.close()

    async def _run_async_write(
        self,
        *,
        operation_name: str,
        operation: Callable[[aiosqlite.Connection], Awaitable[ResultT]],
    ) -> ResultT:
        conn = await self._connect_async()
        try:
            return await run_async_sqlite_write_with_retry(
                conn=conn,
                db_path=self._db_path,
                operation=lambda: operation(conn),
                lock=None,
                repository_name=type(self).__name__,
                operation_name=operation_name,
            )
        finally:
            await conn.close()

    def get_artifact(self, task_id: str) -> TaskArtifact | None:
        """Load the full artifact including all entries."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM task_artifacts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                return None

            artifact_row = dict(row)

            entry_rows = conn.execute(
                "SELECT * FROM task_artifact_entries WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
            entries = [self._row_to_entry(dict(row)) for row in entry_rows]

            evidence_bundle = None
            eb_json = artifact_row.get("evidence_bundle_json")
            if eb_json:
                try:
                    evidence_bundle = VerificationEvidenceBundle.model_validate_json(
                        eb_json
                    )
                except (json.JSONDecodeError, ValueError):
                    get_logger(__name__).debug(
                        "Malformed evidence_bundle_json tolerated; falling back to None"
                    )

            return TaskArtifact(
                task_id=artifact_row["task_id"],
                spec_artifact_id=artifact_row.get("spec_artifact_id", ""),
                entries=entries,
                evidence_bundle=evidence_bundle,
                summary=artifact_row.get("summary", ""),
                created_at=artifact_row.get("created_at", ""),
                updated_at=artifact_row.get("updated_at", ""),
            )
        finally:
            conn.close()

    async def get_artifact_async(self, task_id: str) -> TaskArtifact | None:
        """Load the full artifact including all entries."""
        conn = await self._connect_async()
        try:
            row = await async_fetchone(
                conn,
                "SELECT * FROM task_artifacts WHERE task_id = ?",
                (task_id,),
            )
            if row is None:
                return None

            artifact_row = dict(row)
            entry_rows = await async_fetchall(
                conn,
                "SELECT * FROM task_artifact_entries WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            )
            entries = [self._row_to_entry(dict(row)) for row in entry_rows]

            evidence_bundle = None
            eb_json = artifact_row.get("evidence_bundle_json")
            if eb_json:
                try:
                    evidence_bundle = VerificationEvidenceBundle.model_validate_json(
                        eb_json
                    )
                except (json.JSONDecodeError, ValueError):
                    get_logger(__name__).debug(
                        "Malformed evidence_bundle_json tolerated; falling back to None"
                    )

            return TaskArtifact(
                task_id=artifact_row["task_id"],
                spec_artifact_id=artifact_row.get("spec_artifact_id", ""),
                entries=entries,
                evidence_bundle=evidence_bundle,
                summary=artifact_row.get("summary", ""),
                created_at=artifact_row.get("created_at", ""),
                updated_at=artifact_row.get("updated_at", ""),
            )
        finally:
            await conn.close()

    def get_artifact_summary(self, task_id: str) -> TaskArtifactSummary | None:
        """Compute and return a summary view of the artifact."""
        artifact = self.get_artifact(task_id)
        if artifact is None:
            return None

        phase_counts: dict[str, int] = {}
        for entry in artifact.entries:
            phase = entry.phase.value
            phase_counts[phase] = phase_counts.get(phase, 0) + 1

        evidence_count = 0
        if artifact.evidence_bundle is not None:
            evidence_count = len(artifact.evidence_bundle.items)

        return TaskArtifactSummary(
            task_id=artifact.task_id,
            spec_artifact_id=artifact.spec_artifact_id,
            total_entries=len(artifact.entries),
            phase_counts=phase_counts,
            evidence_item_count=evidence_count,
            has_verification_bundle=artifact.evidence_bundle is not None,
            has_summary=bool(artifact.summary),
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )

    async def get_artifact_summary_async(
        self, task_id: str
    ) -> TaskArtifactSummary | None:
        """Compute and return a summary view of the artifact."""
        artifact = await self.get_artifact_async(task_id)
        if artifact is None:
            return None

        phase_counts: dict[str, int] = {}
        for entry in artifact.entries:
            phase = entry.phase.value
            phase_counts[phase] = phase_counts.get(phase, 0) + 1

        evidence_count = 0
        if artifact.evidence_bundle is not None:
            evidence_count = len(artifact.evidence_bundle.items)

        return TaskArtifactSummary(
            task_id=artifact.task_id,
            spec_artifact_id=artifact.spec_artifact_id,
            total_entries=len(artifact.entries),
            phase_counts=phase_counts,
            evidence_item_count=evidence_count,
            has_verification_bundle=artifact.evidence_bundle is not None,
            has_summary=bool(artifact.summary),
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )

    def ensure_artifact(self, task_id: str, spec_artifact_id: str) -> TaskArtifact:
        """Create the artifact record if it does not exist."""
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR IGNORE INTO task_artifacts "
                "(task_id, spec_artifact_id, summary, "
                "created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?)",
                (task_id, spec_artifact_id, now, now),
            )

        self._run_write(operation_name="ensure_artifact", operation=operation)
        artifact = self.get_artifact(task_id)
        if artifact is None:
            raise RuntimeError(f"Failed to create task artifact for task_id={task_id}")
        return artifact

    async def ensure_artifact_async(
        self, task_id: str, spec_artifact_id: str
    ) -> TaskArtifact:
        """Create the artifact record if it does not exist."""
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "INSERT OR IGNORE INTO task_artifacts "
                "(task_id, spec_artifact_id, summary, "
                "created_at, updated_at) "
                "VALUES (?, ?, '', ?, ?)",
                (task_id, spec_artifact_id, now, now),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="ensure_artifact_async",
            operation=operation,
        )
        artifact = await self.get_artifact_async(task_id)
        if artifact is None:
            raise RuntimeError(f"Failed to create task artifact for task_id={task_id}")
        return artifact

    def append_entry(
        self,
        task_id: str,
        entry: TaskArtifactEntry,
    ) -> int:
        """Append an entry to the artifact."""

        def operation(conn: sqlite3.Connection) -> int:
            conn.execute(
                "INSERT INTO task_artifact_entries "
                "(entry_id, task_id, phase, timestamp, role_id, "
                "instance_id, event_type, description, payload_json, "
                "linked_evidence_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    task_id,
                    entry.phase.value,
                    entry.timestamp,
                    entry.role_id,
                    entry.instance_id,
                    entry.event_type,
                    entry.description,
                    entry.payload_json,
                    json.dumps(list(entry.linked_evidence_ids)),
                ),
            )
            now = datetime.now(tz=timezone.utc).isoformat()
            conn.execute(
                "UPDATE task_artifacts SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            return _last_insert_row_id(conn)

        return self._run_write(operation_name="append_entry", operation=operation)

    async def append_entry_async(
        self,
        task_id: str,
        entry: TaskArtifactEntry,
    ) -> int:
        """Append an entry to the artifact."""

        async def operation(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute(
                "INSERT INTO task_artifact_entries "
                "(entry_id, task_id, phase, timestamp, role_id, "
                "instance_id, event_type, description, payload_json, "
                "linked_evidence_ids) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.entry_id,
                    task_id,
                    entry.phase.value,
                    entry.timestamp,
                    entry.role_id,
                    entry.instance_id,
                    entry.event_type,
                    entry.description,
                    entry.payload_json,
                    json.dumps(list(entry.linked_evidence_ids)),
                ),
            )
            inserted_row_id = cursor.lastrowid
            await cursor.close()
            now = datetime.now(tz=timezone.utc).isoformat()
            cursor = await conn.execute(
                "UPDATE task_artifacts SET updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )
            await cursor.close()
            if inserted_row_id is None:
                raise RuntimeError("SQLite append_entry returned no row id")
            return int(inserted_row_id)

        return await self._run_async_write(
            operation_name="append_entry_async",
            operation=operation,
        )

    def update_evidence_bundle(
        self,
        task_id: str,
        bundle: VerificationEvidenceBundle,
    ) -> None:
        """Update the evidence bundle for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE task_artifacts "
                "SET evidence_bundle_json = ?, updated_at = ? "
                "WHERE task_id = ?",
                (bundle.model_dump_json(), now, task_id),
            )

        self._run_write(operation_name="update_evidence_bundle", operation=operation)

    async def update_evidence_bundle_async(
        self,
        task_id: str,
        bundle: VerificationEvidenceBundle,
    ) -> None:
        """Update the evidence bundle for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "UPDATE task_artifacts "
                "SET evidence_bundle_json = ?, updated_at = ? "
                "WHERE task_id = ?",
                (bundle.model_dump_json(), now, task_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="update_evidence_bundle_async",
            operation=operation,
        )

    def update_summary(
        self,
        task_id: str,
        summary: str,
    ) -> None:
        """Update the summary text for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE task_artifacts SET summary = ?, updated_at = ? "
                "WHERE task_id = ?",
                (summary, now, task_id),
            )

        self._run_write(operation_name="update_summary", operation=operation)

    async def update_summary_async(
        self,
        task_id: str,
        summary: str,
    ) -> None:
        """Update the summary text for an artifact."""
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "UPDATE task_artifacts SET summary = ?, updated_at = ? "
                "WHERE task_id = ?",
                (summary, now, task_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="update_summary_async",
            operation=operation,
        )

    def query_entries(
        self,
        *,
        task_id: str,
        phase: TaskArtifactPhase | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskArtifactEntry], int]:
        """Query artifact entries with optional filters.

        Returns (entries, total_count).
        """
        conn = self._connect()
        try:
            conditions: list[str] = ["task_id = ?"]
            params: list[object] = [task_id]

            if phase is not None:
                conditions.append("phase = ?")
                params.append(phase.value)
            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)

            where = " AND ".join(conditions)
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM task_artifact_entries WHERE {where}",
                tuple(params),
            ).fetchone()
            total = count_row[0] if count_row else 0

            params.append(limit)
            params.append(offset)
            rows = conn.execute(
                f"SELECT * FROM task_artifact_entries "
                f"WHERE {where} "
                f"ORDER BY id ASC LIMIT ? OFFSET ?",
                tuple(params),
            ).fetchall()
            entries = [self._row_to_entry(dict(row)) for row in rows]
            return entries, total
        finally:
            conn.close()

    async def query_entries_async(
        self,
        *,
        task_id: str,
        phase: TaskArtifactPhase | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TaskArtifactEntry], int]:
        """Query artifact entries with optional filters.

        Returns (entries, total_count).
        """
        conn = await self._connect_async()
        try:
            conditions: list[str] = ["task_id = ?"]
            params: list[object] = [task_id]

            if phase is not None:
                conditions.append("phase = ?")
                params.append(phase.value)
            if event_type is not None:
                conditions.append("event_type = ?")
                params.append(event_type)

            where = " AND ".join(conditions)
            count_row = await async_fetchone(
                conn,
                f"SELECT COUNT(*) FROM task_artifact_entries WHERE {where}",
                tuple(params),
            )
            total = int(count_row[0]) if count_row else 0

            params.append(limit)
            params.append(offset)
            rows = await async_fetchall(
                conn,
                f"SELECT * FROM task_artifact_entries "
                f"WHERE {where} "
                f"ORDER BY id ASC LIMIT ? OFFSET ?",
                tuple(params),
            )
            entries = [self._row_to_entry(dict(row)) for row in rows]
            return entries, total
        finally:
            await conn.close()

    @staticmethod
    def _row_to_entry(raw: dict[str, object]) -> TaskArtifactEntry:
        linked_raw = raw.get("linked_evidence_ids", "[]")
        if isinstance(linked_raw, str):
            try:
                linked = tuple(json.loads(linked_raw))
            except json.JSONDecodeError:
                linked = ()
        elif isinstance(linked_raw, (list, tuple)):
            linked = tuple(str(x) for x in linked_raw)
        else:
            linked = ()

        payload_raw = raw.get("payload_json", "{}")
        if isinstance(payload_raw, str):
            payload = payload_raw
        elif isinstance(payload_raw, dict):
            payload = json.dumps(payload_raw)
        else:
            payload = "{}"

        return TaskArtifactEntry(
            entry_id=str(raw.get("entry_id", "")),
            phase=TaskArtifactPhase(
                str(
                    raw.get(
                        "phase",
                        TaskArtifactPhase.EXECUTION.value,
                    )
                )
            ),
            timestamp=str(raw.get("timestamp", "")),
            role_id=str(raw.get("role_id", "")),
            instance_id=str(raw.get("instance_id", "")),
            event_type=str(raw.get("event_type", "")),
            description=str(raw.get("description", "")),
            payload_json=payload,
            linked_evidence_ids=linked,
        )


def _enable_wal_if_available(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        get_logger(__name__).debug(
            "WAL mode is unavailable for task artifacts; using default journal mode"
        )


def _last_insert_row_id(conn: sqlite3.Connection) -> int:
    cursor = conn.execute("SELECT last_insert_rowid()")
    row_id = cursor.fetchone()[0]
    if not isinstance(row_id, int):
        raise RuntimeError("SQLite last_insert_rowid returned a non-integer")
    return row_id
