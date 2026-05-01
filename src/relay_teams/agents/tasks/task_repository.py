from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from relay_teams.agents.tasks.ids import new_task_spec_artifact_id
from relay_teams.agents.tasks.enums import TaskStatus
from relay_teams.agents.tasks.models import (
    TaskEnvelope,
    TaskRecord,
    TaskSpec,
    TaskSpecArtifact,
)
from relay_teams.persistence import async_fetchall, async_fetchone
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository

_SQLITE_SAFE_VARIABLE_LIMIT = 900


def _task_envelope_from_storage(value: object) -> TaskEnvelope | None:
    try:
        return TaskEnvelope.model_validate(json.loads(str(value)))
    except (json.JSONDecodeError, ValueError):
        return None


class TaskRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id              TEXT PRIMARY KEY,
                    trace_id             TEXT NOT NULL,
                    session_id           TEXT NOT NULL,
                    parent_task_id       TEXT,
                    envelope_json        TEXT NOT NULL,
                    status               TEXT NOT NULL,
                    assigned_instance_id TEXT,
                    result               TEXT,
                    error_message        TEXT,
                    created_at           TEXT NOT NULL,
                    updated_at           TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_trace ON tasks(trace_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_session_trace ON tasks(session_id, trace_id, created_at)"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_spec_artifacts (
                    artifact_id    TEXT PRIMARY KEY,
                    task_id        TEXT NOT NULL,
                    trace_id       TEXT NOT NULL,
                    session_id     TEXT NOT NULL,
                    source_task_id TEXT,
                    spec_json      TEXT NOT NULL,
                    version        INTEGER NOT NULL,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_task ON task_spec_artifacts(task_id, version)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_session ON task_spec_artifacts(session_id, updated_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_trace ON task_spec_artifacts(trace_id, updated_at)"
            )

        self._run_write(
            operation_name="init_tables",
            operation=operation,
        )

    async def _init_tables_async(self) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id              TEXT PRIMARY KEY,
                    trace_id             TEXT NOT NULL,
                    session_id           TEXT NOT NULL,
                    parent_task_id       TEXT,
                    envelope_json        TEXT NOT NULL,
                    status               TEXT NOT NULL,
                    assigned_instance_id TEXT,
                    result               TEXT,
                    error_message        TEXT,
                    created_at           TEXT NOT NULL,
                    updated_at           TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_trace ON tasks(trace_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_session_trace ON tasks(session_id, trace_id, created_at)"
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_spec_artifacts (
                    artifact_id    TEXT PRIMARY KEY,
                    task_id        TEXT NOT NULL,
                    trace_id       TEXT NOT NULL,
                    session_id     TEXT NOT NULL,
                    source_task_id TEXT,
                    spec_json      TEXT NOT NULL,
                    version        INTEGER NOT NULL,
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_task ON task_spec_artifacts(task_id, version)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_session ON task_spec_artifacts(session_id, updated_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_spec_artifacts_trace ON task_spec_artifacts(trace_id, updated_at)"
            )

        await self._run_async_write(
            operation_name="init_tables_async",
            operation=lambda _conn: operation(),
        )

    def create(self, envelope: TaskEnvelope) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        stored_envelope = envelope

        def operation() -> None:
            nonlocal stored_envelope
            stored_envelope = self._prepare_envelope_for_storage(
                envelope,
                now=now,
                current_envelope=None,
            )
            self._conn.execute(
                """
                INSERT INTO tasks(task_id, trace_id, session_id, parent_task_id, envelope_json, status,
                                  assigned_instance_id, result, error_message, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_envelope.task_id,
                    stored_envelope.trace_id,
                    stored_envelope.session_id,
                    stored_envelope.parent_task_id,
                    stored_envelope.model_dump_json(),
                    TaskStatus.CREATED.value,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )

        self._run_write(
            operation_name="create",
            operation=operation,
        )
        record = TaskRecord(envelope=stored_envelope)
        return record

    async def create_async(self, envelope: TaskEnvelope) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        stored_envelope = envelope

        async def operation() -> None:
            nonlocal stored_envelope
            conn = await self._get_async_conn()
            stored_envelope = await self._prepare_envelope_for_storage_async(
                conn,
                envelope,
                now=now,
                current_envelope=None,
            )
            cursor = await conn.execute(
                """
                INSERT INTO tasks(task_id, trace_id, session_id, parent_task_id, envelope_json, status,
                                  assigned_instance_id, result, error_message, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored_envelope.task_id,
                    stored_envelope.trace_id,
                    stored_envelope.session_id,
                    stored_envelope.parent_task_id,
                    stored_envelope.model_dump_json(),
                    TaskStatus.CREATED.value,
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="create_async",
            operation=lambda _conn: operation(),
        )
        record = TaskRecord(envelope=stored_envelope)
        return record

    def update_envelope(self, task_id: str, envelope: TaskEnvelope) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation() -> None:
            row = self._conn.execute(
                "SELECT envelope_json FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            current_envelope = _task_envelope_from_storage(row["envelope_json"])
            stored_envelope = self._prepare_envelope_for_storage(
                envelope,
                now=now,
                current_envelope=current_envelope,
            )
            self._conn.execute(
                """
                UPDATE tasks
                SET trace_id=?, session_id=?, parent_task_id=?, envelope_json=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    stored_envelope.trace_id,
                    stored_envelope.session_id,
                    stored_envelope.parent_task_id,
                    stored_envelope.model_dump_json(),
                    now,
                    task_id,
                ),
            )

        self._run_write(
            operation_name="update_envelope",
            operation=operation,
        )
        return self.get(task_id)

    async def update_envelope_async(
        self, task_id: str, envelope: TaskEnvelope
    ) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation() -> None:
            conn = await self._get_async_conn()
            row = await async_fetchone(
                conn,
                "SELECT envelope_json FROM tasks WHERE task_id=?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            current_envelope = _task_envelope_from_storage(row["envelope_json"])
            stored_envelope = await self._prepare_envelope_for_storage_async(
                conn,
                envelope,
                now=now,
                current_envelope=current_envelope,
            )
            cursor = await conn.execute(
                """
                UPDATE tasks
                SET trace_id=?, session_id=?, parent_task_id=?, envelope_json=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    stored_envelope.trace_id,
                    stored_envelope.session_id,
                    stored_envelope.parent_task_id,
                    stored_envelope.model_dump_json(),
                    now,
                    task_id,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="update_envelope_async",
            operation=lambda _conn: operation(),
        )
        return await self.get_async(task_id)

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation() -> None:
            row = self._conn.execute(
                "SELECT assigned_instance_id, result, error_message FROM tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")

            next_assigned_instance_id = (
                assigned_instance_id
                if assigned_instance_id is not None
                else (
                    str(row["assigned_instance_id"])
                    if row["assigned_instance_id"]
                    else None
                )
            )

            if result is not None:
                next_result = result
            elif status == TaskStatus.COMPLETED:
                next_result = str(row["result"]) if row["result"] else None
            else:
                next_result = None

            if error_message is not None:
                next_error_message = error_message
            elif status in {
                TaskStatus.CREATED,
                TaskStatus.ASSIGNED,
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
            }:
                next_error_message = None
            else:
                next_error_message = (
                    str(row["error_message"]) if row["error_message"] else None
                )

            self._conn.execute(
                """
                UPDATE tasks
                SET status=?, assigned_instance_id=?, result=?, error_message=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    status.value,
                    next_assigned_instance_id,
                    next_result,
                    next_error_message,
                    now,
                    task_id,
                ),
            )

        self._run_write(
            operation_name="update_status",
            operation=operation,
        )

    async def update_status_async(
        self,
        task_id: str,
        status: TaskStatus,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation() -> None:
            conn = await self._get_async_conn()
            row = await async_fetchone(
                conn,
                "SELECT assigned_instance_id, result, error_message FROM tasks WHERE task_id=?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")

            next_assigned_instance_id = (
                assigned_instance_id
                if assigned_instance_id is not None
                else (
                    str(row["assigned_instance_id"])
                    if row["assigned_instance_id"]
                    else None
                )
            )

            if result is not None:
                next_result = result
            elif status == TaskStatus.COMPLETED:
                next_result = str(row["result"]) if row["result"] else None
            else:
                next_result = None

            if error_message is not None:
                next_error_message = error_message
            elif status in {
                TaskStatus.CREATED,
                TaskStatus.ASSIGNED,
                TaskStatus.RUNNING,
                TaskStatus.COMPLETED,
            }:
                next_error_message = None
            else:
                next_error_message = (
                    str(row["error_message"]) if row["error_message"] else None
                )

            cursor = await conn.execute(
                """
                UPDATE tasks
                SET status=?, assigned_instance_id=?, result=?, error_message=?, updated_at=?
                WHERE task_id=?
                """,
                (
                    status.value,
                    next_assigned_instance_id,
                    next_result,
                    next_error_message,
                    now,
                    task_id,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="update_status_async",
            operation=lambda _conn: operation(),
        )

    async def heartbeat_running_async(
        self,
        task_id: str,
        assigned_instance_id: str | None = None,
    ) -> bool:
        now = datetime.now(tz=timezone.utc).isoformat()

        async def operation() -> bool:
            conn = await self._get_async_conn()
            if assigned_instance_id is None:
                cursor = await conn.execute(
                    """
                    UPDATE tasks
                    SET updated_at=?
                    WHERE task_id=? AND status=?
                    """,
                    (now, task_id, TaskStatus.RUNNING.value),
                )
            else:
                cursor = await conn.execute(
                    """
                    UPDATE tasks
                    SET updated_at=?
                    WHERE task_id=? AND status=? AND assigned_instance_id=?
                    """,
                    (
                        now,
                        task_id,
                        TaskStatus.RUNNING.value,
                        assigned_instance_id,
                    ),
                )
            try:
                updated = cursor.rowcount > 0
            finally:
                await cursor.close()
            if updated:
                return True
            row = await async_fetchone(
                conn,
                "SELECT task_id FROM tasks WHERE task_id=?",
                (task_id,),
            )
            if row is None:
                raise KeyError(f"Unknown task_id: {task_id}")
            return False

        return await self._run_async_write(
            operation_name="heartbeat_running_async",
            operation=lambda _conn: operation(),
        )

    def get(self, task_id: str) -> TaskRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return self._to_record(row)

    async def get_async(self, task_id: str) -> TaskRecord:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM tasks WHERE task_id=?",
                (task_id,),
            )
        )
        if row is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return self._to_record(row)

    def list_all(self) -> tuple[TaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at ASC"
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    async def list_all_async(self) -> tuple[TaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT * FROM tasks ORDER BY created_at ASC",
            )
        )
        return tuple(self._to_record(row) for row in rows)

    def list_by_trace(self, trace_id: str) -> tuple[TaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE trace_id=? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    async def list_by_trace_async(self, trace_id: str) -> tuple[TaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT * FROM tasks WHERE trace_id=? ORDER BY created_at ASC",
                (trace_id,),
            )
        )
        return tuple(self._to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[TaskRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_session_run_ids(
        self,
        session_id: str,
        run_ids: tuple[str, ...],
    ) -> tuple[TaskRecord, ...]:
        normalized_run_ids = tuple(
            dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip())
        )
        if not normalized_run_ids:
            return ()
        rows: list[sqlite3.Row] = []
        chunk_size = _SQLITE_SAFE_VARIABLE_LIMIT - 1
        with self._lock:
            for index in range(0, len(normalized_run_ids), chunk_size):
                run_id_chunk = normalized_run_ids[index : index + chunk_size]
                placeholders = ", ".join("?" for _ in run_id_chunk)
                rows.extend(
                    self._conn.execute(
                        f"SELECT * FROM tasks WHERE session_id=? AND trace_id IN ({placeholders}) ORDER BY created_at ASC",
                        (session_id, *run_id_chunk),
                    ).fetchall()
                )
        rows.sort(key=lambda row: str(row["created_at"] or ""))
        return tuple(self._to_record(row) for row in rows)

    async def list_by_session_run_ids_async(
        self,
        session_id: str,
        run_ids: tuple[str, ...],
    ) -> tuple[TaskRecord, ...]:
        normalized_run_ids = tuple(
            dict.fromkeys(run_id.strip() for run_id in run_ids if run_id.strip())
        )
        if not normalized_run_ids:
            return ()
        rows: list[sqlite3.Row] = []
        chunk_size = _SQLITE_SAFE_VARIABLE_LIMIT - 1

        async def operation() -> tuple[TaskRecord, ...]:
            conn = await self._get_async_conn()
            for index in range(0, len(normalized_run_ids), chunk_size):
                run_id_chunk = normalized_run_ids[index : index + chunk_size]
                placeholders = ", ".join("?" for _ in run_id_chunk)
                rows.extend(
                    await async_fetchall(
                        conn,
                        f"SELECT * FROM tasks WHERE session_id=? AND trace_id IN ({placeholders}) ORDER BY created_at ASC",
                        (session_id, *run_id_chunk),
                    )
                )
            rows.sort(key=lambda row: str(row["created_at"] or ""))
            return tuple(self._to_record(row) for row in rows)

        return await self._run_async_read(lambda _conn: operation())

    async def list_by_session_async(self, session_id: str) -> tuple[TaskRecord, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at ASC",
                (session_id,),
            )
        )
        return tuple(self._to_record(row) for row in rows)

    def delete_by_session(self, session_id: str) -> None:
        def operation() -> None:
            self._conn.execute(
                "DELETE FROM task_spec_artifacts WHERE session_id=?",
                (session_id,),
            )
            self._conn.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))

        self._run_write(operation_name="delete_by_session", operation=operation)

    async def delete_by_session_async(self, session_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM task_spec_artifacts WHERE session_id=?",
                (session_id,),
            )
            await cursor.close()
            cursor = await conn.execute(
                "DELETE FROM tasks WHERE session_id=?", (session_id,)
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_by_session_async",
            operation=lambda _conn: operation(),
        )

    def delete(self, task_id: str) -> None:
        def operation() -> None:
            self._conn.execute(
                "DELETE FROM task_spec_artifacts WHERE task_id=?",
                (task_id,),
            )
            self._conn.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))

        self._run_write(operation_name="delete", operation=operation)

    async def delete_async(self, task_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                "DELETE FROM task_spec_artifacts WHERE task_id=?",
                (task_id,),
            )
            await cursor.close()
            cursor = await conn.execute(
                "DELETE FROM tasks WHERE task_id=?",
                (task_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_async",
            operation=lambda _conn: operation(),
        )

    def get_spec_artifact(self, artifact_id: str) -> TaskSpecArtifact:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task_spec_artifacts WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown spec artifact_id: {artifact_id}")
        return self._to_spec_artifact(row)

    async def get_spec_artifact_async(self, artifact_id: str) -> TaskSpecArtifact:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM task_spec_artifacts WHERE artifact_id=?",
                (artifact_id,),
            )
        )
        if row is None:
            raise KeyError(f"Unknown spec artifact_id: {artifact_id}")
        return self._to_spec_artifact(row)

    def get_latest_spec_artifact_for_task(self, task_id: str) -> TaskSpecArtifact:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM task_spec_artifacts
                WHERE task_id=?
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No spec artifact found for task_id: {task_id}")
        return self._to_spec_artifact(row)

    async def get_latest_spec_artifact_for_task_async(
        self,
        task_id: str,
    ) -> TaskSpecArtifact:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT * FROM task_spec_artifacts
                WHERE task_id=?
                ORDER BY version DESC, updated_at DESC
                LIMIT 1
                """,
                (task_id,),
            )
        )
        if row is None:
            raise KeyError(f"No spec artifact found for task_id: {task_id}")
        return self._to_spec_artifact(row)

    def list_spec_artifacts_by_task(self, task_id: str) -> tuple[TaskSpecArtifact, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM task_spec_artifacts
                WHERE task_id=?
                ORDER BY version ASC, created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return tuple(self._to_spec_artifact(row) for row in rows)

    async def list_spec_artifacts_by_task_async(
        self,
        task_id: str,
    ) -> tuple[TaskSpecArtifact, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT * FROM task_spec_artifacts
                WHERE task_id=?
                ORDER BY version ASC, created_at ASC
                """,
                (task_id,),
            )
        )
        return tuple(self._to_spec_artifact(row) for row in rows)

    def _prepare_envelope_for_storage(
        self,
        envelope: TaskEnvelope,
        *,
        now: str,
        current_envelope: TaskEnvelope | None,
    ) -> TaskEnvelope:
        if envelope.spec is None:
            return envelope.model_copy(
                update={"spec_artifact_id": None, "spec_source_task_id": None}
            )
        if (
            current_envelope is not None
            and current_envelope.spec == envelope.spec
            and current_envelope.spec_artifact_id is not None
            and (
                envelope.spec_artifact_id is None
                or envelope.spec_artifact_id == current_envelope.spec_artifact_id
            )
        ):
            return envelope.model_copy(
                update={
                    "spec_artifact_id": current_envelope.spec_artifact_id,
                    "spec_source_task_id": envelope.spec_source_task_id
                    or current_envelope.spec_source_task_id,
                }
            )
        if envelope.spec_artifact_id is not None and not (
            current_envelope is not None
            and envelope.spec_artifact_id == current_envelope.spec_artifact_id
        ):
            row = self._conn.execute(
                "SELECT * FROM task_spec_artifacts WHERE artifact_id=?",
                (envelope.spec_artifact_id,),
            ).fetchone()
            if row is not None:
                artifact = self._to_spec_artifact(row)
                self._validate_reusable_spec_artifact(
                    artifact=artifact,
                    envelope=envelope,
                )
                return envelope.model_copy(
                    update={
                        "spec": artifact.spec,
                        "spec_artifact_id": artifact.artifact_id,
                        "spec_source_task_id": envelope.spec_source_task_id
                        or artifact.source_task_id
                        or artifact.task_id,
                    }
                )
        latest_version = (
            current_envelope.spec.prompt_artifact_version
            if current_envelope is not None and current_envelope.spec is not None
            else 0
        )
        next_version = latest_version + 1
        spec = envelope.spec.model_copy(
            update={"prompt_artifact_version": next_version}
        )
        artifact_id = envelope.spec_artifact_id or new_task_spec_artifact_id().value
        if (
            current_envelope is not None
            and artifact_id == current_envelope.spec_artifact_id
        ):
            artifact_id = new_task_spec_artifact_id().value
        source_task_id = envelope.spec_source_task_id
        self._conn.execute(
            """
            INSERT INTO task_spec_artifacts(
                artifact_id,
                task_id,
                trace_id,
                session_id,
                source_task_id,
                spec_json,
                version,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                envelope.task_id,
                envelope.trace_id,
                envelope.session_id,
                source_task_id,
                spec.model_dump_json(),
                next_version,
                now,
                now,
            ),
        )
        return envelope.model_copy(
            update={
                "spec": spec,
                "spec_artifact_id": artifact_id,
                "spec_source_task_id": source_task_id,
            }
        )

    async def _prepare_envelope_for_storage_async(
        self,
        conn: aiosqlite.Connection,
        envelope: TaskEnvelope,
        *,
        now: str,
        current_envelope: TaskEnvelope | None,
    ) -> TaskEnvelope:
        if envelope.spec is None:
            return envelope.model_copy(
                update={"spec_artifact_id": None, "spec_source_task_id": None}
            )
        if (
            current_envelope is not None
            and current_envelope.spec == envelope.spec
            and current_envelope.spec_artifact_id is not None
            and (
                envelope.spec_artifact_id is None
                or envelope.spec_artifact_id == current_envelope.spec_artifact_id
            )
        ):
            return envelope.model_copy(
                update={
                    "spec_artifact_id": current_envelope.spec_artifact_id,
                    "spec_source_task_id": envelope.spec_source_task_id
                    or current_envelope.spec_source_task_id,
                }
            )
        if envelope.spec_artifact_id is not None and not (
            current_envelope is not None
            and envelope.spec_artifact_id == current_envelope.spec_artifact_id
        ):
            row = await async_fetchone(
                conn,
                "SELECT * FROM task_spec_artifacts WHERE artifact_id=?",
                (envelope.spec_artifact_id,),
            )
            if row is not None:
                artifact = self._to_spec_artifact(row)
                self._validate_reusable_spec_artifact(
                    artifact=artifact,
                    envelope=envelope,
                )
                return envelope.model_copy(
                    update={
                        "spec": artifact.spec,
                        "spec_artifact_id": artifact.artifact_id,
                        "spec_source_task_id": envelope.spec_source_task_id
                        or artifact.source_task_id
                        or artifact.task_id,
                    }
                )
        latest_version = (
            current_envelope.spec.prompt_artifact_version
            if current_envelope is not None and current_envelope.spec is not None
            else 0
        )
        next_version = latest_version + 1
        spec = envelope.spec.model_copy(
            update={"prompt_artifact_version": next_version}
        )
        artifact_id = envelope.spec_artifact_id or new_task_spec_artifact_id().value
        if (
            current_envelope is not None
            and artifact_id == current_envelope.spec_artifact_id
        ):
            artifact_id = new_task_spec_artifact_id().value
        source_task_id = envelope.spec_source_task_id
        cursor = await conn.execute(
            """
            INSERT INTO task_spec_artifacts(
                artifact_id,
                task_id,
                trace_id,
                session_id,
                source_task_id,
                spec_json,
                version,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                envelope.task_id,
                envelope.trace_id,
                envelope.session_id,
                source_task_id,
                spec.model_dump_json(),
                next_version,
                now,
                now,
            ),
        )
        await cursor.close()
        return envelope.model_copy(
            update={
                "spec": spec,
                "spec_artifact_id": artifact_id,
                "spec_source_task_id": source_task_id,
            }
        )

    @staticmethod
    def _validate_reusable_spec_artifact(
        *,
        artifact: TaskSpecArtifact,
        envelope: TaskEnvelope,
    ) -> None:
        if artifact.task_id != envelope.task_id:
            raise ValueError("spec_artifact_id references a different task")
        if artifact.spec != envelope.spec:
            raise ValueError("spec_artifact_id references a different task spec")

    def _to_record(self, row: sqlite3.Row) -> TaskRecord:
        envelope_data = json.loads(str(row["envelope_json"]))
        return TaskRecord(
            envelope=TaskEnvelope.model_validate(envelope_data),
            status=TaskStatus(str(row["status"])),
            assigned_instance_id=str(row["assigned_instance_id"])
            if row["assigned_instance_id"]
            else None,
            result=str(row["result"]) if row["result"] else None,
            error_message=str(row["error_message"]) if row["error_message"] else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def _to_spec_artifact(row: sqlite3.Row) -> TaskSpecArtifact:
        return TaskSpecArtifact(
            artifact_id=str(row["artifact_id"]),
            task_id=str(row["task_id"]),
            session_id=str(row["session_id"]),
            trace_id=str(row["trace_id"]),
            source_task_id=str(row["source_task_id"])
            if row["source_task_id"]
            else None,
            spec=TaskSpec.model_validate(json.loads(str(row["spec_json"]))),
            version=int(row["version"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
