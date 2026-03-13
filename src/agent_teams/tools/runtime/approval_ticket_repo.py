from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.persistence.db import open_sqlite


class ApprovalTicketStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    COMPLETED = "completed"


class ApprovalTicketRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: str = Field(min_length=1)
    signature_key: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    instance_id: str = Field(min_length=1)
    role_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    args_preview: str = ""
    status: ApprovalTicketStatus = ApprovalTicketStatus.REQUESTED
    feedback: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    resolved_at: datetime | None = None


def approval_signature_key(
    *,
    run_id: str,
    task_id: str,
    instance_id: str,
    role_id: str,
    tool_name: str,
    args_preview: str,
) -> str:
    raw = "||".join(
        [
            run_id.strip(),
            task_id.strip(),
            instance_id.strip(),
            role_id.strip(),
            tool_name.strip(),
            args_preview.strip(),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ApprovalTicketRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_tickets (
                tool_call_id   TEXT PRIMARY KEY,
                signature_key  TEXT NOT NULL,
                run_id         TEXT NOT NULL,
                session_id     TEXT NOT NULL,
                task_id        TEXT NOT NULL,
                instance_id    TEXT NOT NULL,
                role_id        TEXT NOT NULL,
                tool_name      TEXT NOT NULL,
                args_preview   TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL,
                feedback       TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                resolved_at    TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_tickets_run_status ON approval_tickets(run_id, status, created_at ASC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_tickets_session_status ON approval_tickets(session_id, status, created_at ASC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_approval_tickets_signature ON approval_tickets(signature_key, updated_at DESC)"
        )
        self._conn.commit()

    def upsert_requested(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        session_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        args_preview: str,
    ) -> ApprovalTicketRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        signature_key = approval_signature_key(
            run_id=run_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_name=tool_name,
            args_preview=args_preview,
        )
        existing = self.get(tool_call_id)
        created_at = existing.created_at.isoformat() if existing is not None else now
        resolved_at = (
            existing.resolved_at.isoformat()
            if existing and existing.resolved_at
            else None
        )
        self._conn.execute(
            """
            INSERT INTO approval_tickets(tool_call_id, signature_key, run_id, session_id, task_id, instance_id,
                                         role_id, tool_name, args_preview, status, feedback, created_at, updated_at, resolved_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_call_id)
            DO UPDATE SET
                signature_key=excluded.signature_key,
                run_id=excluded.run_id,
                session_id=excluded.session_id,
                task_id=excluded.task_id,
                instance_id=excluded.instance_id,
                role_id=excluded.role_id,
                tool_name=excluded.tool_name,
                args_preview=excluded.args_preview,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                tool_call_id,
                signature_key,
                run_id,
                session_id,
                task_id,
                instance_id,
                role_id,
                tool_name,
                args_preview,
                ApprovalTicketStatus.REQUESTED.value,
                "",
                created_at,
                now,
                resolved_at,
            ),
        )
        self._conn.commit()
        record = self.get(tool_call_id)
        if record is None:
            raise RuntimeError(f"Failed to persist approval ticket {tool_call_id}")
        return record

    def resolve(
        self,
        *,
        tool_call_id: str,
        status: ApprovalTicketStatus,
        feedback: str = "",
    ) -> ApprovalTicketRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        resolved_at = now if status != ApprovalTicketStatus.REQUESTED else None
        self._conn.execute(
            """
            UPDATE approval_tickets
            SET status=?, feedback=?, updated_at=?, resolved_at=?
            WHERE tool_call_id=?
            """,
            (status.value, feedback, now, resolved_at, tool_call_id),
        )
        self._conn.commit()
        record = self.get(tool_call_id)
        if record is None:
            raise KeyError(f"Unknown approval ticket: {tool_call_id}")
        return record

    def mark_completed(self, tool_call_id: str) -> ApprovalTicketRecord | None:
        record = self.get(tool_call_id)
        if record is None:
            return None
        return self.resolve(
            tool_call_id=tool_call_id,
            status=ApprovalTicketStatus.COMPLETED,
            feedback=record.feedback,
        )

    def get(self, tool_call_id: str) -> ApprovalTicketRecord | None:
        row = self._conn.execute(
            "SELECT * FROM approval_tickets WHERE tool_call_id=?",
            (tool_call_id,),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def list_open_by_run(self, run_id: str) -> tuple[ApprovalTicketRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM approval_tickets WHERE run_id=? AND status=? ORDER BY created_at ASC",
            (run_id, ApprovalTicketStatus.REQUESTED.value),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_open_by_session(self, session_id: str) -> tuple[ApprovalTicketRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM approval_tickets WHERE session_id=? AND status=? ORDER BY created_at ASC",
            (session_id, ApprovalTicketStatus.REQUESTED.value),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def find_reusable(
        self,
        *,
        run_id: str,
        task_id: str,
        instance_id: str,
        role_id: str,
        tool_name: str,
        args_preview: str,
    ) -> ApprovalTicketRecord | None:
        signature_key = approval_signature_key(
            run_id=run_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_name=tool_name,
            args_preview=args_preview,
        )
        row = self._conn.execute(
            """
            SELECT * FROM approval_tickets
            WHERE signature_key=?
              AND status IN (?, ?)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (
                signature_key,
                ApprovalTicketStatus.REQUESTED.value,
                ApprovalTicketStatus.APPROVED.value,
            ),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM approval_tickets WHERE session_id=?", (session_id,)
        )
        self._conn.commit()

    def _to_record(self, row: sqlite3.Row) -> ApprovalTicketRecord:
        resolved_at_raw = row["resolved_at"]
        return ApprovalTicketRecord(
            tool_call_id=str(row["tool_call_id"]),
            signature_key=str(row["signature_key"]),
            run_id=str(row["run_id"]),
            session_id=str(row["session_id"]),
            task_id=str(row["task_id"]),
            instance_id=str(row["instance_id"]),
            role_id=str(row["role_id"]),
            tool_name=str(row["tool_name"]),
            args_preview=str(row["args_preview"]),
            status=ApprovalTicketStatus(str(row["status"])),
            feedback=str(row["feedback"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            resolved_at=(
                datetime.fromisoformat(str(resolved_at_raw))
                if resolved_at_raw
                else None
            ),
        )
