# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.validation import (
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)
from relay_teams.workspace.ssh_profile_models import (
    SshProfileRecord,
    SshProfileStoredConfig,
)

LOGGER = get_logger(__name__)


class SshProfileRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ssh_profiles (
                    ssh_profile_id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    username TEXT,
                    port INTEGER,
                    remote_shell TEXT,
                    connect_timeout_seconds INTEGER,
                    private_key_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(
                table_name="ssh_profiles",
                column_name="private_key_name",
                column_type="TEXT",
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SshProfileRepository",
            operation_name="init_tables",
        )

    def list_all(self) -> tuple[SshProfileRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ssh_profiles ORDER BY ssh_profile_id ASC"
            ).fetchall()
        records: list[SshProfileRecord] = []
        for row in rows:
            try:
                records.append(self._to_record(row))
            except ValueError as exc:
                _log_invalid_profile_row(row=row, error=exc)
        return tuple(records)

    async def list_all_async(self) -> tuple[SshProfileRecord, ...]:
        return await self._call_sync_async(self.list_all)

    def get(self, ssh_profile_id: str) -> SshProfileRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ssh_profiles WHERE ssh_profile_id=?",
                (ssh_profile_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown ssh_profile_id: {ssh_profile_id}")
        try:
            return self._to_record(row)
        except ValueError as exc:
            _log_invalid_profile_row(row=row, error=exc)
            raise KeyError(f"Unknown ssh_profile_id: {ssh_profile_id}") from exc

    async def get_async(self, ssh_profile_id: str) -> SshProfileRecord:
        return await self._call_sync_async(self.get, ssh_profile_id)

    def save(
        self,
        *,
        ssh_profile_id: str,
        config: SshProfileStoredConfig,
    ) -> SshProfileRecord:
        existing = None
        try:
            existing = self.get(ssh_profile_id)
        except KeyError:
            existing = None
        created_at = (
            existing.created_at
            if existing is not None
            else datetime.now(tz=timezone.utc)
        )
        updated_at = datetime.now(tz=timezone.utc)
        record = SshProfileRecord(
            ssh_profile_id=ssh_profile_id,
            host=config.host,
            username=config.username,
            port=config.port,
            remote_shell=config.remote_shell,
            connect_timeout_seconds=config.connect_timeout_seconds,
            private_key_name=config.private_key_name,
            created_at=created_at,
            updated_at=updated_at,
        )

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO ssh_profiles(
                    ssh_profile_id,
                    host,
                    username,
                    port,
                    remote_shell,
                    connect_timeout_seconds,
                    private_key_name,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ssh_profile_id) DO UPDATE SET
                    host=excluded.host,
                    username=excluded.username,
                    port=excluded.port,
                    remote_shell=excluded.remote_shell,
                    connect_timeout_seconds=excluded.connect_timeout_seconds,
                    private_key_name=excluded.private_key_name,
                    updated_at=excluded.updated_at
                """,
                (
                    record.ssh_profile_id,
                    record.host,
                    record.username,
                    record.port,
                    record.remote_shell,
                    record.connect_timeout_seconds,
                    record.private_key_name,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SshProfileRepository",
            operation_name="save",
        )
        return record

    async def save_async(
        self, *, ssh_profile_id: str, config: SshProfileStoredConfig
    ) -> SshProfileRecord:
        return await self._call_sync_async(
            self.save, ssh_profile_id=ssh_profile_id, config=config
        )

    def delete(self, ssh_profile_id: str) -> None:
        def operation() -> None:
            self._conn.execute(
                "DELETE FROM ssh_profiles WHERE ssh_profile_id=?",
                (ssh_profile_id,),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="SshProfileRepository",
            operation_name="delete",
        )

    async def delete_async(self, ssh_profile_id: str) -> None:
        return await self._call_sync_async(self.delete, ssh_profile_id)

    def exists(self, ssh_profile_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM ssh_profiles WHERE ssh_profile_id=?",
                (ssh_profile_id,),
            ).fetchone()
        return row is not None

    async def exists_async(self, ssh_profile_id: str) -> bool:
        return await self._call_sync_async(self.exists, ssh_profile_id)

    def _to_record(self, row: sqlite3.Row) -> SshProfileRecord:
        ssh_profile_id = require_persisted_identifier(
            row["ssh_profile_id"],
            field_name="ssh_profile_id",
        )
        created_at = parse_persisted_datetime_or_none(row["created_at"])
        updated_at = parse_persisted_datetime_or_none(row["updated_at"])
        if created_at is None or updated_at is None:
            raise ValueError("Invalid ssh profile timestamp")
        return SshProfileRecord(
            ssh_profile_id=ssh_profile_id,
            host=str(row["host"]).strip(),
            username=_normalize_optional_text(row["username"]),
            port=_normalize_optional_int(row["port"]),
            remote_shell=_normalize_optional_text(row["remote_shell"]),
            connect_timeout_seconds=_normalize_optional_int(
                row["connect_timeout_seconds"]
            ),
            private_key_name=_normalize_optional_text(row["private_key_name"]),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _ensure_column(
        self,
        *,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        rows = self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        for row in rows:
            if str(row["name"]).strip() == column_name:
                return
        self._conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    normalized = str(value).strip()
    if not normalized:
        return None
    return int(normalized)


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_profile_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "ssh_profile_id": _persisted_value_preview(row["ssh_profile_id"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="workspace.ssh_profile_repository.row_invalid",
        message="Skipping invalid persisted ssh profile row",
        payload=payload,
    )
