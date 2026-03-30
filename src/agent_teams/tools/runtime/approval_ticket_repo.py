from __future__ import annotations

import hashlib
import sqlite3
import logging
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from agent_teams.logger import get_logger, log_event
from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.validation import (
    RequiredIdentifierStr,
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class ApprovalTicketStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    COMPLETED = "completed"


class ApprovalTicketRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_call_id: RequiredIdentifierStr
    signature_key: RequiredIdentifierStr
    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    task_id: RequiredIdentifierStr
    instance_id: RequiredIdentifierStr
    role_id: RequiredIdentifierStr
    tool_name: RequiredIdentifierStr
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
        self._db_path = Path(db_path)
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
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

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ApprovalTicketRepository",
            operation_name="init_tables",
        )

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

        def operation() -> None:
            existing = self.get(tool_call_id)
            created_at = (
                existing.created_at.isoformat() if existing is not None else now
            )
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

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ApprovalTicketRepository",
            operation_name="upsert_requested",
        )
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
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                UPDATE approval_tickets
                SET status=?, feedback=?, updated_at=?, resolved_at=?
                WHERE tool_call_id=?
                """,
                (status.value, feedback, now, resolved_at, tool_call_id),
            ),
            lock=self._lock,
            repository_name="ApprovalTicketRepository",
            operation_name="resolve",
        )
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
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approval_tickets WHERE tool_call_id=?",
                (tool_call_id,),
            ).fetchone()
        if row is None:
            return None
        return self._record_or_none(row)

    def list_open_by_run(self, run_id: str) -> tuple[ApprovalTicketRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approval_tickets WHERE run_id=? AND status=? ORDER BY created_at ASC",
                (run_id, ApprovalTicketStatus.REQUESTED.value),
            ).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def list_open_by_session(self, session_id: str) -> tuple[ApprovalTicketRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM approval_tickets WHERE session_id=? AND status=? ORDER BY created_at ASC",
                (session_id, ApprovalTicketStatus.REQUESTED.value),
            ).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

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
        with self._lock:
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
        return self._record_or_none(row)

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM approval_tickets WHERE session_id=?", (session_id,)
            ),
            lock=self._lock,
            repository_name="ApprovalTicketRepository",
            operation_name="delete_by_session",
        )

    def _to_record(self, row: sqlite3.Row) -> ApprovalTicketRecord:
        return ApprovalTicketRecord(
            tool_call_id=require_persisted_identifier(
                row["tool_call_id"],
                field_name="tool_call_id",
            ),
            signature_key=require_persisted_identifier(
                row["signature_key"],
                field_name="signature_key",
            ),
            run_id=require_persisted_identifier(row["run_id"], field_name="run_id"),
            session_id=require_persisted_identifier(
                row["session_id"],
                field_name="session_id",
            ),
            task_id=require_persisted_identifier(row["task_id"], field_name="task_id"),
            instance_id=require_persisted_identifier(
                row["instance_id"],
                field_name="instance_id",
            ),
            role_id=require_persisted_identifier(row["role_id"], field_name="role_id"),
            tool_name=require_persisted_identifier(
                row["tool_name"],
                field_name="tool_name",
            ),
            args_preview=str(row["args_preview"]),
            status=ApprovalTicketStatus(str(row["status"])),
            feedback=str(row["feedback"]),
            created_at=_require_ticket_timestamp(
                row=row,
                tool_call_id=normalize_persisted_text(row["tool_call_id"])
                or "<invalid>",
                field_name="created_at",
            ),
            updated_at=_require_ticket_timestamp(
                row=row,
                tool_call_id=normalize_persisted_text(row["tool_call_id"])
                or "<invalid>",
                field_name="updated_at",
            ),
            resolved_at=(
                _optional_ticket_timestamp(
                    row=row,
                    tool_call_id=normalize_persisted_text(row["tool_call_id"])
                    or "<invalid>",
                    field_name="resolved_at",
                )
            ),
        )

    def _record_or_none(self, row: sqlite3.Row) -> ApprovalTicketRecord | None:
        try:
            return self._to_record(row)
        except (ValidationError, ValueError) as exc:
            _log_invalid_ticket_row(row=row, error=exc)
            return None


def _require_ticket_timestamp(
    *,
    row: sqlite3.Row,
    tool_call_id: str,
    field_name: str,
) -> datetime:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_ticket_timestamp(
        tool_call_id=tool_call_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _optional_ticket_timestamp(
    *,
    row: sqlite3.Row,
    tool_call_id: str,
    field_name: str,
) -> datetime | None:
    raw_value = row[field_name]
    normalized = normalize_persisted_text(raw_value)
    if normalized is None:
        return None
    parsed = parse_persisted_datetime_or_none(raw_value)
    if parsed is not None:
        return parsed
    _log_invalid_ticket_timestamp(
        tool_call_id=tool_call_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(raw_value),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_ticket_timestamp(
    *,
    tool_call_id: str,
    field_name: str,
    raw_preview: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "tool_call_id": tool_call_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="tools.approval_ticket_repo.timestamp_invalid",
        message="Invalid persisted approval ticket timestamp",
        payload=payload,
    )


def _log_invalid_ticket_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "tool_call_id": _persisted_value_preview(row["tool_call_id"]),
        "run_id": _persisted_value_preview(row["run_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "resolved_at": _persisted_value_preview(row["resolved_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="tools.approval_ticket_repo.row_invalid",
        message="Skipping invalid persisted approval ticket row",
        payload=payload,
    )
