from __future__ import annotations

import hashlib
import json
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
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
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
    cache_key: str = "",
) -> str:
    signature_args = cache_key.strip() or args_preview.strip()
    raw = "||".join(
        [
            run_id.strip(),
            task_id.strip(),
            instance_id.strip(),
            role_id.strip(),
            tool_name.strip(),
            signature_args,
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
                    metadata_json  TEXT NOT NULL DEFAULT '{}',
                    status         TEXT NOT NULL,
                    feedback       TEXT NOT NULL DEFAULT '',
                    created_at     TEXT NOT NULL,
                    updated_at     TEXT NOT NULL,
                    resolved_at    TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute(
                    "PRAGMA table_info(approval_tickets)"
                ).fetchall()
            }
            if "metadata_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE approval_tickets "
                    "ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
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
        metadata: dict[str, JsonValue] | None = None,
        cache_key: str = "",
        signature_args_preview: str | None = None,
    ) -> ApprovalTicketRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        metadata_json = json.dumps(
            {} if metadata is None else metadata,
            ensure_ascii=False,
            sort_keys=True,
        )
        signature_key = approval_signature_key(
            run_id=run_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_name=tool_name,
            args_preview=(
                args_preview
                if signature_args_preview is None
                else signature_args_preview
            ),
            cache_key=cache_key,
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
                                             role_id, tool_name, args_preview, metadata_json, status, feedback, created_at, updated_at, resolved_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    metadata_json=excluded.metadata_json,
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
                    metadata_json,
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
        return self._record_or_none(row, fallback_invalid_timestamps=True)

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
        cache_key: str = "",
        signature_args_preview: str | None = None,
    ) -> ApprovalTicketRecord | None:
        signature_key = approval_signature_key(
            run_id=run_id,
            task_id=task_id,
            instance_id=instance_id,
            role_id=role_id,
            tool_name=tool_name,
            args_preview=(
                args_preview
                if signature_args_preview is None
                else signature_args_preview
            ),
            cache_key=cache_key,
        )
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM approval_tickets
                WHERE signature_key=?
                  AND status IN (?, ?)
                ORDER BY updated_at DESC
                """,
                (
                    signature_key,
                    ApprovalTicketStatus.REQUESTED.value,
                    ApprovalTicketStatus.APPROVED.value,
                ),
            ).fetchall()
        for row in rows:
            record = self._record_or_none(row, fallback_invalid_timestamps=True)
            if record is not None:
                return record
        return None

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

    def _to_record(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> ApprovalTicketRecord:
        tool_call_id = require_persisted_identifier(
            row["tool_call_id"],
            field_name="tool_call_id",
        )
        status = ApprovalTicketStatus(str(row["status"]))
        created_at, updated_at = _load_ticket_timestamps(
            row=row,
            tool_call_id=tool_call_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        resolved_at = _optional_ticket_timestamp(
            row=row,
            tool_call_id=tool_call_id,
            field_name="resolved_at",
            fallback_invalid_timestamps=fallback_invalid_timestamps,
            fallback_value=(
                updated_at if status != ApprovalTicketStatus.REQUESTED else None
            ),
        )
        return ApprovalTicketRecord(
            tool_call_id=tool_call_id,
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
            metadata=_load_ticket_metadata(row["metadata_json"]),
            status=status,
            feedback=str(row["feedback"]),
            created_at=created_at,
            updated_at=updated_at,
            resolved_at=resolved_at,
        )

    def _record_or_none(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> ApprovalTicketRecord | None:
        try:
            return self._to_record(
                row,
                fallback_invalid_timestamps=fallback_invalid_timestamps,
            )
        except (ValidationError, ValueError) as exc:
            _log_invalid_ticket_row(row=row, error=exc)
            return None


def _load_ticket_timestamps(
    *,
    row: sqlite3.Row,
    tool_call_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            _log_invalid_ticket_timestamp(
                tool_call_id=tool_call_id,
                field_name="created_at",
                raw_preview=_persisted_value_preview(row["created_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            _log_invalid_ticket_timestamp(
                tool_call_id=tool_call_id,
                field_name="updated_at",
                raw_preview=_persisted_value_preview(row["updated_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted updated_at")
        return (
            created_at,
            updated_at,
        )
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_ticket_timestamp(
            tool_call_id=tool_call_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_ticket_timestamp(
            tool_call_id=tool_call_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
            fallback_iso=updated_at.isoformat(),
        )
    return (
        created_at,
        updated_at,
    )


def _optional_ticket_timestamp(
    *,
    row: sqlite3.Row,
    tool_call_id: str,
    field_name: str,
    fallback_invalid_timestamps: bool = False,
    fallback_value: datetime | None = None,
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
        fallback_iso=fallback_value.isoformat() if fallback_value is not None else None,
    )
    if fallback_invalid_timestamps:
        return fallback_value
    raise ValueError(f"Invalid persisted {field_name}")


def _load_ticket_metadata(raw_value: object) -> dict[str, JsonValue]:
    normalized = normalize_persisted_text(raw_value)
    if normalized is None:
        return {}
    try:
        decoded = json.loads(normalized)
    except ValueError as exc:
        raise ValueError("Invalid persisted metadata_json") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Approval ticket metadata_json must decode to an object")
    metadata: dict[str, JsonValue] = {}
    for key, value in decoded.items():
        metadata[str(key)] = value
    return metadata


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_ticket_timestamp(
    *,
    tool_call_id: str,
    field_name: str,
    raw_preview: str,
    fallback_iso: str | None,
) -> None:
    payload: dict[str, JsonValue] = {
        "tool_call_id": tool_call_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="tools.approval_ticket_repo.timestamp_invalid",
        message=(
            "Using fallback for invalid persisted approval ticket timestamp"
            if fallback_iso is not None
            else "Invalid persisted approval ticket timestamp"
        ),
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
        "metadata_json": _persisted_value_preview(row["metadata_json"]),
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
