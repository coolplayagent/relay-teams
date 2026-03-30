# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import JsonValue, ValidationError

from agent_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpConnectionRecord,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from agent_teams.logger import get_logger, log_event
from agent_teams.persistence.db import open_sqlite
from agent_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class GatewaySessionRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gateway_sessions (
                gateway_session_id   TEXT PRIMARY KEY,
                channel_type         TEXT NOT NULL,
                external_session_id  TEXT NOT NULL,
                internal_session_id  TEXT NOT NULL,
                active_run_id        TEXT,
                peer_user_id         TEXT,
                peer_chat_id         TEXT,
                cwd                  TEXT,
                capabilities_json    TEXT NOT NULL,
                channel_state_json   TEXT NOT NULL,
                session_mcp_servers_json TEXT NOT NULL,
                mcp_connections_json TEXT NOT NULL,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_gateway_sessions_channel_external
            ON gateway_sessions(channel_type, external_session_id)
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gateway_sessions_internal_session
            ON gateway_sessions(internal_session_id)
            """
        )
        self._conn.commit()

    def create(self, record: GatewaySessionRecord) -> GatewaySessionRecord:
        self._conn.execute(
            """
            INSERT INTO gateway_sessions(
                gateway_session_id,
                channel_type,
                external_session_id,
                internal_session_id,
                active_run_id,
                peer_user_id,
                peer_chat_id,
                cwd,
                capabilities_json,
                channel_state_json,
                session_mcp_servers_json,
                mcp_connections_json,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.gateway_session_id,
                record.channel_type.value,
                record.external_session_id,
                record.internal_session_id,
                record.active_run_id,
                record.peer_user_id,
                record.peer_chat_id,
                record.cwd,
                json.dumps(record.capabilities, ensure_ascii=False),
                json.dumps(record.channel_state, ensure_ascii=False),
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in record.session_mcp_servers
                    ],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [item.model_dump(mode="json") for item in record.mcp_connections],
                    ensure_ascii=False,
                ),
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
            ),
        )
        self._conn.commit()
        return record

    def get(self, gateway_session_id: str) -> GatewaySessionRecord:
        row = self._conn.execute(
            "SELECT * FROM gateway_sessions WHERE gateway_session_id=?",
            (gateway_session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown gateway_session_id: {gateway_session_id}")
        try:
            return self._to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_gateway_session_row(row=row, error=exc)
            raise KeyError(f"Unknown gateway_session_id: {gateway_session_id}") from exc

    def get_by_external(
        self,
        *,
        channel_type: GatewayChannelType,
        external_session_id: str,
    ) -> GatewaySessionRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM gateway_sessions
            WHERE channel_type=? AND external_session_id=?
            """,
            (channel_type.value, external_session_id),
        ).fetchone()
        if row is None:
            return None
        return self._record_or_none(row, fallback_invalid_timestamps=True)

    def get_by_internal_session_id(
        self,
        internal_session_id: str,
    ) -> GatewaySessionRecord | None:
        rows = self._conn.execute(
            """
            SELECT * FROM gateway_sessions
            WHERE internal_session_id=?
            ORDER BY updated_at DESC
            """,
            (internal_session_id,),
        ).fetchall()
        for row in rows:
            record = self._record_or_none(row, fallback_invalid_timestamps=True)
            if record is not None:
                return record
        return None

    def update(self, record: GatewaySessionRecord) -> GatewaySessionRecord:
        cursor = self._conn.execute(
            """
            UPDATE gateway_sessions
            SET external_session_id=?,
                internal_session_id=?,
                active_run_id=?,
                peer_user_id=?,
                peer_chat_id=?,
                cwd=?,
                capabilities_json=?,
                channel_state_json=?,
                session_mcp_servers_json=?,
                mcp_connections_json=?,
                updated_at=?
            WHERE gateway_session_id=?
            """,
            (
                record.external_session_id,
                record.internal_session_id,
                record.active_run_id,
                record.peer_user_id,
                record.peer_chat_id,
                record.cwd,
                json.dumps(record.capabilities, ensure_ascii=False),
                json.dumps(record.channel_state, ensure_ascii=False),
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in record.session_mcp_servers
                    ],
                    ensure_ascii=False,
                ),
                json.dumps(
                    [item.model_dump(mode="json") for item in record.mcp_connections],
                    ensure_ascii=False,
                ),
                record.updated_at.isoformat(),
                record.gateway_session_id,
            ),
        )
        self._conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"Unknown gateway_session_id: {record.gateway_session_id}")
        return record

    def list_all(self) -> tuple[GatewaySessionRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM gateway_sessions ORDER BY created_at DESC"
        ).fetchall()
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def _to_record(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> GatewaySessionRecord:
        capabilities_raw = json.loads(
            normalize_persisted_text(row["capabilities_json"]) or "{}"
        )
        channel_state_raw = json.loads(
            normalize_persisted_text(row["channel_state_json"]) or "{}"
        )
        mcp_servers_raw = json.loads(
            normalize_persisted_text(row["session_mcp_servers_json"]) or "[]"
        )
        connections_raw = json.loads(
            normalize_persisted_text(row["mcp_connections_json"]) or "[]"
        )
        mcp_servers = tuple(
            GatewayMcpServerSpec.model_validate(item)
            for item in mcp_servers_raw
            if isinstance(item, dict)
        )
        mcp_connections = tuple(
            GatewayMcpConnectionRecord.model_validate(item)
            for item in connections_raw
            if isinstance(item, dict)
        )
        gateway_session_id = require_persisted_identifier(
            row["gateway_session_id"],
            field_name="gateway_session_id",
        )
        created_at, updated_at = _load_gateway_session_timestamps(
            row=row,
            gateway_session_id=gateway_session_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        return GatewaySessionRecord(
            gateway_session_id=gateway_session_id,
            channel_type=GatewayChannelType(str(row["channel_type"])),
            external_session_id=require_persisted_identifier(
                row["external_session_id"],
                field_name="external_session_id",
            ),
            internal_session_id=require_persisted_identifier(
                row["internal_session_id"],
                field_name="internal_session_id",
            ),
            active_run_id=normalize_persisted_text(row["active_run_id"]),
            peer_user_id=normalize_persisted_text(row["peer_user_id"]),
            peer_chat_id=normalize_persisted_text(row["peer_chat_id"]),
            cwd=normalize_persisted_text(row["cwd"]),
            capabilities=capabilities_raw if isinstance(capabilities_raw, dict) else {},
            channel_state=(
                channel_state_raw if isinstance(channel_state_raw, dict) else {}
            ),
            session_mcp_servers=mcp_servers,
            mcp_connections=mcp_connections,
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)

    def _record_or_none(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> GatewaySessionRecord | None:
        try:
            return self._to_record(
                row,
                fallback_invalid_timestamps=fallback_invalid_timestamps,
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_gateway_session_row(row=row, error=exc)
            return None


def _load_gateway_session_timestamps(
    *,
    row: sqlite3.Row,
    gateway_session_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            _log_invalid_gateway_session_timestamp(
                gateway_session_id=gateway_session_id,
                field_name="created_at",
                raw_preview=_persisted_value_preview(row["created_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            _log_invalid_gateway_session_timestamp(
                gateway_session_id=gateway_session_id,
                field_name="updated_at",
                raw_preview=_persisted_value_preview(row["updated_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted updated_at")
        return created_at, updated_at
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_gateway_session_timestamp(
            gateway_session_id=gateway_session_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_gateway_session_timestamp(
            gateway_session_id=gateway_session_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
            fallback_iso=updated_at.isoformat(),
        )
    return created_at, updated_at


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_gateway_session_timestamp(
    *,
    gateway_session_id: str,
    field_name: str,
    raw_preview: str,
    fallback_iso: str | None,
) -> None:
    payload: dict[str, JsonValue] = {
        "gateway_session_id": gateway_session_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.session_repository.timestamp_invalid",
        message=(
            "Using fallback for invalid persisted gateway session timestamp"
            if fallback_iso is not None
            else "Invalid persisted gateway session timestamp"
        ),
        payload=payload,
    )


def _log_invalid_gateway_session_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "gateway_session_id": _persisted_value_preview(row["gateway_session_id"]),
        "external_session_id": _persisted_value_preview(row["external_session_id"]),
        "internal_session_id": _persisted_value_preview(row["internal_session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.session_repository.row_invalid",
        message="Skipping invalid persisted gateway session row",
        payload=payload,
    )
