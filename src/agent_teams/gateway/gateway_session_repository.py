# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.gateway.gateway_models import (
    GatewayChannelType,
    GatewayMcpConnectionRecord,
    GatewayMcpServerSpec,
    GatewaySessionRecord,
)
from agent_teams.persistence.db import open_sqlite


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
        return self._to_record(row)

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
        return self._to_record(row)

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
        return tuple(self._to_record(row) for row in rows)

    def _to_record(self, row: sqlite3.Row) -> GatewaySessionRecord:
        mcp_servers_raw = json.loads(str(row["session_mcp_servers_json"]))
        connections_raw = json.loads(str(row["mcp_connections_json"]))
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
        return GatewaySessionRecord(
            gateway_session_id=str(row["gateway_session_id"]),
            channel_type=GatewayChannelType(str(row["channel_type"])),
            external_session_id=str(row["external_session_id"]),
            internal_session_id=str(row["internal_session_id"]),
            active_run_id=(
                str(row["active_run_id"]) if row["active_run_id"] is not None else None
            ),
            peer_user_id=(
                str(row["peer_user_id"]) if row["peer_user_id"] is not None else None
            ),
            peer_chat_id=(
                str(row["peer_chat_id"]) if row["peer_chat_id"] is not None else None
            ),
            cwd=str(row["cwd"]) if row["cwd"] is not None else None,
            capabilities=json.loads(str(row["capabilities_json"])),
            channel_state=json.loads(str(row["channel_state_json"])),
            session_mcp_servers=mcp_servers,
            mcp_connections=mcp_connections,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)
