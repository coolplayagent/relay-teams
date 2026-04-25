# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.session_history_marker_models import (
    SessionHistoryMarkerRecord,
    SessionHistoryMarkerType,
)
from relay_teams.validation import (
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class SessionHistoryMarkerRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_history_markers (
                    marker_id      TEXT PRIMARY KEY,
                    session_id     TEXT NOT NULL,
                    marker_type    TEXT NOT NULL,
                    metadata_json  TEXT NOT NULL DEFAULT '{}',
                    created_at     TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_history_markers_session
                ON session_history_markers(session_id, created_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="init_tables",
        )

    def create(
        self,
        *,
        session_id: str,
        marker_type: SessionHistoryMarkerType,
        metadata: dict[str, str] | None = None,
    ) -> SessionHistoryMarkerRecord:
        now = datetime.now(tz=timezone.utc)
        record = SessionHistoryMarkerRecord(
            marker_id=f"marker-{uuid.uuid4().hex}",
            session_id=session_id,
            marker_type=marker_type,
            metadata={} if metadata is None else dict(metadata),
            created_at=now,
        )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO session_history_markers(
                    marker_id,
                    session_id,
                    marker_type,
                    metadata_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    record.marker_id,
                    record.session_id,
                    record.marker_type.value,
                    json.dumps(record.metadata, ensure_ascii=False),
                    record.created_at.isoformat(),
                ),
            ),
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="create",
        )
        return record

    async def create_async(
        self,
        *,
        session_id: str,
        marker_type: SessionHistoryMarkerType,
        metadata: dict[str, str] | None = None,
    ) -> SessionHistoryMarkerRecord:
        return await self._call_sync_async(
            self.create,
            session_id=session_id,
            marker_type=marker_type,
            metadata=metadata,
        )

    def create_clear_marker(self, session_id: str) -> SessionHistoryMarkerRecord:
        return self.create(
            session_id=session_id,
            marker_type=SessionHistoryMarkerType.CLEAR,
        )

    async def create_clear_marker_async(
        self, session_id: str
    ) -> SessionHistoryMarkerRecord:
        return await self._call_sync_async(self.create_clear_marker, session_id)

    def list_by_session(
        self,
        session_id: str,
    ) -> tuple[SessionHistoryMarkerRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT marker_id, session_id, marker_type, metadata_json, created_at
                FROM session_history_markers
                WHERE session_id=?
                ORDER BY created_at ASC, marker_id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    async def list_by_session_async(
        self, session_id: str
    ) -> tuple[SessionHistoryMarkerRecord, ...]:
        return await self._call_sync_async(self.list_by_session, session_id)

    def get_latest(
        self,
        session_id: str,
        *,
        marker_type: SessionHistoryMarkerType | None = None,
    ) -> SessionHistoryMarkerRecord | None:
        query = """
            SELECT marker_id, session_id, marker_type, metadata_json, created_at
            FROM session_history_markers
            WHERE session_id=?
            """
        params: tuple[str, ...]
        if marker_type is None:
            params = (session_id,)
        else:
            query += " AND marker_type=?"
            params = (session_id, marker_type.value)
        query += " ORDER BY created_at DESC, marker_id DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        for row in rows:
            record = self._record_or_none(row)
            if record is not None:
                return record
        return None

    async def get_latest_async(
        self, session_id: str, *, marker_type: SessionHistoryMarkerType | None = None
    ) -> SessionHistoryMarkerRecord | None:
        return await self._call_sync_async(
            self.get_latest, session_id, marker_type=marker_type
        )

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM session_history_markers WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="delete_by_session",
        )

    async def delete_by_session_async(self, session_id: str) -> None:
        return await self._call_sync_async(self.delete_by_session, session_id)

    def delete_by_conversation(self, session_id: str, conversation_id: str) -> None:
        def operation() -> None:
            rows = self._conn.execute(
                """
                SELECT marker_id, metadata_json
                FROM session_history_markers
                WHERE session_id=? AND marker_type=?
                """,
                (
                    session_id,
                    SessionHistoryMarkerType.COMPACTION.value,
                ),
            ).fetchall()
            marker_ids = [
                str(row["marker_id"])
                for row in rows
                if _load_metadata(str(row["metadata_json"])).get("conversation_id")
                == conversation_id
            ]
            if not marker_ids:
                return
            placeholders = ",".join("?" for _ in marker_ids)
            self._conn.execute(
                f"DELETE FROM session_history_markers WHERE marker_id IN ({placeholders})",
                marker_ids,
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SessionHistoryMarkerRepository",
            operation_name="delete_by_conversation",
        )

    async def delete_by_conversation_async(
        self, session_id: str, conversation_id: str
    ) -> None:
        return await self._call_sync_async(
            self.delete_by_conversation, session_id, conversation_id
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> SessionHistoryMarkerRecord:
        metadata = _load_metadata(str(row["metadata_json"]))
        return SessionHistoryMarkerRecord(
            marker_id=require_persisted_identifier(
                row["marker_id"],
                field_name="marker_id",
            ),
            session_id=require_persisted_identifier(
                row["session_id"],
                field_name="session_id",
            ),
            marker_type=SessionHistoryMarkerType(str(row["marker_type"])),
            metadata=metadata,
            created_at=_require_history_marker_timestamp(
                row=row,
                marker_id=str(row["marker_id"]),
                field_name="created_at",
            ),
        )

    def _record_or_none(self, row: sqlite3.Row) -> SessionHistoryMarkerRecord | None:
        try:
            return self._to_record(row)
        except (ValidationError, ValueError) as exc:
            _log_invalid_history_marker_row(row=row, error=exc)
            return None


def _load_metadata(raw_value: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in parsed.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _require_history_marker_timestamp(
    *,
    row: sqlite3.Row,
    marker_id: str,
    field_name: str,
) -> datetime:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_history_marker_timestamp(
        marker_id=marker_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_history_marker_timestamp(
    *,
    marker_id: str,
    field_name: str,
    raw_preview: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "marker_id": marker_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.history_marker_repository.timestamp_invalid",
        message="Invalid persisted session history marker timestamp",
        payload=payload,
    )


def _log_invalid_history_marker_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "marker_id": _persisted_value_preview(row["marker_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.history_marker_repository.row_invalid",
        message="Skipping invalid persisted session history marker row",
        payload=payload,
    )
