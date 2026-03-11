from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.state.db import open_sqlite
from agent_teams.workflow.enums import TaskStatus
from agent_teams.workflow.models import TaskEnvelope, TaskRecord


class TaskRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
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
        self._conn.commit()

    def create(self, envelope: TaskEnvelope) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        record = TaskRecord(envelope=envelope)
        self._conn.execute(
            """
            INSERT INTO tasks(task_id, trace_id, session_id, parent_task_id, envelope_json, status,
                              assigned_instance_id, result, error_message, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                envelope.task_id,
                envelope.trace_id,
                envelope.session_id,
                envelope.parent_task_id,
                envelope.model_dump_json(),
                TaskStatus.CREATED.value,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        self._conn.commit()
        return record

    def update_envelope(self, task_id: str, envelope: TaskEnvelope) -> TaskRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE tasks
            SET trace_id=?, session_id=?, parent_task_id=?, envelope_json=?, updated_at=?
            WHERE task_id=?
            """,
            (
                envelope.trace_id,
                envelope.session_id,
                envelope.parent_task_id,
                envelope.model_dump_json(),
                now,
                task_id,
            ),
        )
        self._conn.commit()
        return self.get(task_id)

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        assigned_instance_id: str | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
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
        self._conn.commit()

    def get(self, task_id: str) -> TaskRecord:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return self._to_record(row)

    def list_all(self) -> tuple[TaskRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at ASC"
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_trace(self, trace_id: str) -> tuple[TaskRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE trace_id=? ORDER BY created_at ASC",
            (trace_id,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_by_session(self, session_id: str) -> tuple[TaskRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE session_id=? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute("DELETE FROM tasks WHERE session_id=?", (session_id,))
        self._conn.commit()

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
