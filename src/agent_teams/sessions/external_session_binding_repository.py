# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.sessions.external_session_binding_models import (
    ExternalSessionBinding,
)


class ExternalSessionBindingRepository:
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
                CREATE TABLE IF NOT EXISTS external_session_bindings (
                    platform          TEXT NOT NULL,
                    trigger_id        TEXT NOT NULL,
                    tenant_key        TEXT NOT NULL,
                    external_chat_id  TEXT NOT NULL,
                    session_id        TEXT NOT NULL,
                    created_at        TEXT NOT NULL,
                    updated_at        TEXT NOT NULL,
                    PRIMARY KEY (platform, trigger_id, tenant_key, external_chat_id)
                )
                """
            )
            columns = [
                str(row["name"])
                for row in self._conn.execute(
                    "PRAGMA table_info(external_session_bindings)"
                ).fetchall()
            ]
            if "trigger_id" not in columns:
                self._conn.execute("DROP TABLE IF EXISTS external_session_bindings")
                self._conn.execute(
                    """
                    CREATE TABLE external_session_bindings (
                        platform          TEXT NOT NULL,
                        trigger_id        TEXT NOT NULL,
                        tenant_key        TEXT NOT NULL,
                        external_chat_id  TEXT NOT NULL,
                        session_id        TEXT NOT NULL,
                        created_at        TEXT NOT NULL,
                        updated_at        TEXT NOT NULL,
                        PRIMARY KEY (platform, trigger_id, tenant_key, external_chat_id)
                    )
                    """
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_external_session_bindings_session
                ON external_session_bindings(session_id)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_external_session_bindings_trigger
                ON external_session_bindings(trigger_id, updated_at DESC)
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ExternalSessionBindingRepository",
            operation_name="init_tables",
        )

    def get_binding(
        self,
        *,
        platform: str,
        trigger_id: str,
        tenant_key: str,
        external_chat_id: str,
    ) -> ExternalSessionBinding | None:
        row = self._conn.execute(
            """
            SELECT *
            FROM external_session_bindings
            WHERE platform=? AND trigger_id=? AND tenant_key=? AND external_chat_id=?
            """,
            (platform, trigger_id, tenant_key, external_chat_id),
        ).fetchone()
        if row is None:
            return None
        return self._to_record(row)

    def upsert_binding(
        self,
        *,
        platform: str,
        trigger_id: str,
        tenant_key: str,
        external_chat_id: str,
        session_id: str,
    ) -> ExternalSessionBinding:
        now = datetime.now(tz=timezone.utc).isoformat()
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO external_session_bindings(
                    platform,
                    trigger_id,
                    tenant_key,
                    external_chat_id,
                    session_id,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, trigger_id, tenant_key, external_chat_id)
                DO UPDATE SET
                    session_id=excluded.session_id,
                    updated_at=excluded.updated_at
                """,
                (
                    platform,
                    trigger_id,
                    tenant_key,
                    external_chat_id,
                    session_id,
                    now,
                    now,
                ),
            ),
            lock=self._lock,
            repository_name="ExternalSessionBindingRepository",
            operation_name="upsert_binding",
        )
        binding = self.get_binding(
            platform=platform,
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
        )
        if binding is None:
            raise RuntimeError("Failed to load upserted external session binding")
        return binding

    def list_by_platform(self, platform: str) -> tuple[ExternalSessionBinding, ...]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM external_session_bindings
            WHERE platform=?
            ORDER BY updated_at DESC
            """,
            (platform,),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def exists(
        self,
        *,
        platform: str,
        trigger_id: str,
        tenant_key: str,
        external_chat_id: str,
    ) -> bool:
        return (
            self.get_binding(
                platform=platform,
                trigger_id=trigger_id,
                tenant_key=tenant_key,
                external_chat_id=external_chat_id,
            )
            is not None
        )

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM external_session_bindings WHERE session_id=?",
                (session_id,),
            ),
            lock=self._lock,
            repository_name="ExternalSessionBindingRepository",
            operation_name="delete_by_session",
        )

    def delete_by_trigger(self, trigger_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM external_session_bindings WHERE trigger_id=?",
                (trigger_id,),
            ),
            lock=self._lock,
            repository_name="ExternalSessionBindingRepository",
            operation_name="delete_by_trigger",
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> ExternalSessionBinding:
        return ExternalSessionBinding(
            platform=str(row["platform"]),
            trigger_id=str(row["trigger_id"]),
            tenant_key=str(row["tenant_key"]),
            external_chat_id=str(row["external_chat_id"]),
            session_id=str(row["session_id"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
