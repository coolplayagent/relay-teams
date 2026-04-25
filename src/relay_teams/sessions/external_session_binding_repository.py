# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.sessions.external_session_binding_models import (
    ExternalSessionBinding,
)
from relay_teams.validation import (
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class ExternalSessionBindingRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
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
        return self._record_or_none(row, fallback_invalid_timestamps=True)

    async def get_binding_async(
        self, *, platform: str, trigger_id: str, tenant_key: str, external_chat_id: str
    ) -> ExternalSessionBinding | None:
        return await self._call_sync_async(
            self.get_binding,
            platform=platform,
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
        )

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

    async def upsert_binding_async(
        self,
        *,
        platform: str,
        trigger_id: str,
        tenant_key: str,
        external_chat_id: str,
        session_id: str,
    ) -> ExternalSessionBinding:
        return await self._call_sync_async(
            self.upsert_binding,
            platform=platform,
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
            session_id=session_id,
        )

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
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    async def list_by_platform_async(
        self, platform: str
    ) -> tuple[ExternalSessionBinding, ...]:
        return await self._call_sync_async(self.list_by_platform, platform)

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

    async def exists_async(
        self, *, platform: str, trigger_id: str, tenant_key: str, external_chat_id: str
    ) -> bool:
        return await self._call_sync_async(
            self.exists,
            platform=platform,
            trigger_id=trigger_id,
            tenant_key=tenant_key,
            external_chat_id=external_chat_id,
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

    async def delete_by_session_async(self, session_id: str) -> None:
        return await self._call_sync_async(self.delete_by_session, session_id)

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

    async def delete_by_trigger_async(self, trigger_id: str) -> None:
        return await self._call_sync_async(self.delete_by_trigger, trigger_id)

    @staticmethod
    def _to_record(
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> ExternalSessionBinding:
        trigger_id = require_persisted_identifier(
            row["trigger_id"],
            field_name="trigger_id",
        )
        created_at, updated_at = _load_binding_timestamps(
            row=row,
            trigger_id=trigger_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        return ExternalSessionBinding(
            platform=require_persisted_identifier(
                row["platform"], field_name="platform"
            ),
            trigger_id=trigger_id,
            tenant_key=require_persisted_identifier(
                row["tenant_key"],
                field_name="tenant_key",
            ),
            external_chat_id=require_persisted_identifier(
                row["external_chat_id"],
                field_name="external_chat_id",
            ),
            session_id=require_persisted_identifier(
                row["session_id"],
                field_name="session_id",
            ),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _record_or_none(
        self,
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> ExternalSessionBinding | None:
        try:
            return self._to_record(
                row,
                fallback_invalid_timestamps=fallback_invalid_timestamps,
            )
        except (ValidationError, ValueError) as exc:
            _log_invalid_binding_row(row=row, error=exc)
            return None


def _load_binding_timestamps(
    *,
    row: sqlite3.Row,
    trigger_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            _log_invalid_binding_timestamp(
                trigger_id=trigger_id,
                field_name="created_at",
                raw_preview=_persisted_value_preview(row["created_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            _log_invalid_binding_timestamp(
                trigger_id=trigger_id,
                field_name="updated_at",
                raw_preview=_persisted_value_preview(row["updated_at"]),
                fallback_iso=None,
            )
            raise ValueError("Invalid persisted updated_at")
        return created_at, updated_at
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_binding_timestamp(
            trigger_id=trigger_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_binding_timestamp(
            trigger_id=trigger_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
            fallback_iso=updated_at.isoformat(),
        )
    return created_at, updated_at


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_binding_timestamp(
    *,
    trigger_id: str,
    field_name: str,
    raw_preview: str,
    fallback_iso: str | None,
) -> None:
    payload: dict[str, JsonValue] = {
        "trigger_id": trigger_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.external_session_binding_repository.timestamp_invalid",
        message=(
            "Using fallback for invalid persisted external session binding timestamp"
            if fallback_iso is not None
            else "Invalid persisted external session binding timestamp"
        ),
        payload=payload,
    )


def _log_invalid_binding_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "platform": _persisted_value_preview(row["platform"]),
        "trigger_id": _persisted_value_preview(row["trigger_id"]),
        "session_id": _persisted_value_preview(row["session_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="sessions.external_session_binding_repository.row_invalid",
        message="Skipping invalid persisted external session binding row",
        payload=payload,
    )
