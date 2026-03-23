# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.persistence.db import open_sqlite
from agent_teams.sessions.external_session_binding_models import (
    ExternalSessionBinding,
)


class ExternalSessionBindingRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_session_bindings (
                platform          TEXT NOT NULL,
                tenant_key        TEXT NOT NULL,
                external_chat_id  TEXT NOT NULL,
                session_id        TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                PRIMARY KEY (platform, tenant_key, external_chat_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_external_session_bindings_session
            ON external_session_bindings(session_id)
            """
        )
        self._conn.commit()

    def get_binding(
        self,
        *,
        platform: str,
        tenant_key: str,
        external_chat_id: str,
    ) -> ExternalSessionBinding | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM external_session_bindings
            WHERE platform=? AND tenant_key=? AND external_chat_id=?
            """,
            (platform, tenant_key, external_chat_id),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def upsert_binding(
        self,
        *,
        platform: str,
        tenant_key: str,
        external_chat_id: str,
        session_id: str,
    ) -> ExternalSessionBinding:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO external_session_bindings(
                platform,
                tenant_key,
                external_chat_id,
                session_id,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, tenant_key, external_chat_id)
            DO UPDATE SET
                session_id=excluded.session_id,
                updated_at=excluded.updated_at
            """,
            (
                platform,
                tenant_key,
                external_chat_id,
                session_id,
                now,
                now,
            ),
        )
        self._conn.commit()
        binding = self.get_binding(
            platform=platform,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
        )
        if binding is None:
            raise RuntimeError("Failed to load upserted external session binding")
        return binding

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM external_session_bindings WHERE session_id=?",
            (session_id,),
        )
        self._conn.commit()

    @staticmethod
    def _to_record(row: sqlite3.Row) -> ExternalSessionBinding:
        return ExternalSessionBinding(
            platform=str(row["platform"]),
            tenant_key=str(row["tenant_key"]),
            external_chat_id=str(row["external_chat_id"]),
            session_id=str(row["session_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
