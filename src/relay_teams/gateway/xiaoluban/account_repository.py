# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from pydantic import JsonValue, ValidationError

from relay_teams.gateway.xiaoluban.models import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
    XiaolubanImConfig,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)
from relay_teams.validation import (
    normalize_identifier_tuple,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class XiaolubanAccountRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS xiaoluban_accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    derived_uid TEXT NOT NULL,
                    notification_workspace_ids_json TEXT NOT NULL DEFAULT '[]',
                    notification_receiver TEXT,
                    notification_receivers_json TEXT NOT NULL DEFAULT '[]',
                    notify_self INTEGER NOT NULL DEFAULT 1,
                    im_config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._ensure_column(
                "xiaoluban_accounts",
                "notification_workspace_ids_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                "xiaoluban_accounts",
                "notification_receiver",
                "TEXT",
            )
            self._ensure_column(
                "xiaoluban_accounts",
                "notification_receivers_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )
            self._ensure_column(
                "xiaoluban_accounts",
                "notify_self",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                "xiaoluban_accounts",
                "im_config_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="XiaolubanAccountRepository",
            operation_name="init_tables",
        )

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in columns):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def list_accounts(self) -> tuple[XiaolubanAccountRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM xiaoluban_accounts ORDER BY created_at DESC"
        ).fetchall()
        records: list[XiaolubanAccountRecord] = []
        for row in rows:
            try:
                records.append(self._to_record(row))
            except (ValidationError, ValueError) as exc:
                _log_invalid_row(row=row, error=exc)
        return tuple(records)

    async def list_accounts_async(self) -> tuple[XiaolubanAccountRecord, ...]:
        async def operation(
            conn: aiosqlite.Connection,
        ) -> tuple[XiaolubanAccountRecord, ...]:
            rows = await async_fetchall(
                conn,
                "SELECT * FROM xiaoluban_accounts ORDER BY created_at DESC",
            )
            records: list[XiaolubanAccountRecord] = []
            for row in rows:
                try:
                    records.append(self._to_record(row))
                except (ValidationError, ValueError) as exc:
                    _log_invalid_row(row=row, error=exc)
            return tuple(records)

        return await self._run_async_read(operation)

    def get_account(self, account_id: str) -> XiaolubanAccountRecord:
        row = self._conn.execute(
            "SELECT * FROM xiaoluban_accounts WHERE account_id=?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Xiaoluban account_id: {account_id}")
        try:
            return self._to_record(row)
        except (ValidationError, ValueError) as exc:
            _log_invalid_row(row=row, error=exc)
            raise KeyError(f"Unknown Xiaoluban account_id: {account_id}") from exc

    async def get_account_async(self, account_id: str) -> XiaolubanAccountRecord:
        async def operation(conn: aiosqlite.Connection) -> XiaolubanAccountRecord:
            row = await async_fetchone(
                conn,
                "SELECT * FROM xiaoluban_accounts WHERE account_id=?",
                (account_id,),
            )
            if row is None:
                raise KeyError(f"Unknown Xiaoluban account_id: {account_id}")
            try:
                return self._to_record(row)
            except (ValidationError, ValueError) as exc:
                _log_invalid_row(row=row, error=exc)
                raise KeyError(f"Unknown Xiaoluban account_id: {account_id}") from exc

        return await self._run_async_read(operation)

    def upsert_account(self, record: XiaolubanAccountRecord) -> XiaolubanAccountRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO xiaoluban_accounts(
                    account_id,
                    display_name,
                    base_url,
                    status,
                    derived_uid,
                    notification_workspace_ids_json,
                    notification_receiver,
                    notification_receivers_json,
                    notify_self,
                    im_config_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    base_url=excluded.base_url,
                    status=excluded.status,
                    derived_uid=excluded.derived_uid,
                    notification_workspace_ids_json=excluded.notification_workspace_ids_json,
                    notification_receiver=excluded.notification_receiver,
                    notification_receivers_json=excluded.notification_receivers_json,
                    notify_self=excluded.notify_self,
                    im_config_json=excluded.im_config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.account_id,
                    record.display_name,
                    record.base_url,
                    record.status.value,
                    record.derived_uid,
                    _workspace_ids_to_json(record.notification_workspace_ids),
                    None,
                    _notification_receivers_to_json(record.notification_receivers),
                    1 if record.notify_self else 0,
                    _im_config_to_json(record.im_config),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            ),
            lock=self._lock,
            repository_name="XiaolubanAccountRepository",
            operation_name="upsert_account",
        )
        return self.get_account(record.account_id)

    async def upsert_account_async(
        self, record: XiaolubanAccountRecord
    ) -> XiaolubanAccountRecord:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                INSERT INTO xiaoluban_accounts(
                    account_id,
                    display_name,
                    base_url,
                    status,
                    derived_uid,
                    notification_workspace_ids_json,
                    notification_receiver,
                    notification_receivers_json,
                    notify_self,
                    im_config_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    base_url=excluded.base_url,
                    status=excluded.status,
                    derived_uid=excluded.derived_uid,
                    notification_workspace_ids_json=excluded.notification_workspace_ids_json,
                    notification_receiver=excluded.notification_receiver,
                    notification_receivers_json=excluded.notification_receivers_json,
                    notify_self=excluded.notify_self,
                    im_config_json=excluded.im_config_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.account_id,
                    record.display_name,
                    record.base_url,
                    record.status.value,
                    record.derived_uid,
                    _workspace_ids_to_json(record.notification_workspace_ids),
                    None,
                    _notification_receivers_to_json(record.notification_receivers),
                    1 if record.notify_self else 0,
                    _im_config_to_json(record.im_config),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="upsert_account_async",
            operation=operation,
        )
        return await self.get_account_async(record.account_id)

    def delete_account(self, account_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM xiaoluban_accounts WHERE account_id=?",
                (account_id,),
            ),
            lock=self._lock,
            repository_name="XiaolubanAccountRepository",
            operation_name="delete_account",
        )

    async def delete_account_async(self, account_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM xiaoluban_accounts WHERE account_id=?",
                (account_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_account_async",
            operation=operation,
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> XiaolubanAccountRecord:
        account_id = require_persisted_identifier(
            row["account_id"],
            field_name="account_id",
        )
        derived_uid = require_persisted_identifier(
            row["derived_uid"],
            field_name="derived_uid",
        )
        created_at = parse_persisted_datetime_or_none(row["created_at"])
        updated_at = parse_persisted_datetime_or_none(row["updated_at"])
        if created_at is None:
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            raise ValueError("Invalid persisted updated_at")
        legacy_receiver = (
            str(row["notification_receiver"]).strip()
            if row["notification_receiver"] is not None
            else ""
        )
        notification_receivers = _notification_receivers_from_json(
            str(row["notification_receivers_json"] or "[]")
        )
        notify_self = bool(int(row["notify_self"] or 0))
        if not notification_receivers and legacy_receiver:
            notification_receivers = (legacy_receiver,)
            notify_self = True
        return XiaolubanAccountRecord(
            account_id=account_id,
            display_name=str(row["display_name"]),
            base_url=str(row["base_url"]),
            status=XiaolubanAccountStatus(str(row["status"])),
            derived_uid=derived_uid,
            notification_workspace_ids=_workspace_ids_from_json(
                str(row["notification_workspace_ids_json"] or "[]")
            ),
            notification_receivers=notification_receivers,
            notify_self=notify_self,
            im_config=_im_config_from_json(str(row["im_config_json"] or "{}")),
            created_at=created_at,
            updated_at=updated_at,
        )

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "account_id": _persisted_value_preview(row["account_id"]),
        "derived_uid": _persisted_value_preview(row["derived_uid"]),
        "notification_workspace_ids_json": _persisted_value_preview(
            row["notification_workspace_ids_json"]
        ),
        "im_config_json": _persisted_value_preview(row["im_config_json"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.xiaoluban.account_repository.row_invalid",
        message="Skipping invalid persisted Xiaoluban account row",
        payload=payload,
    )


def _workspace_ids_to_json(workspace_ids: tuple[str, ...]) -> str:
    return json.dumps(list(workspace_ids), ensure_ascii=False)


def _workspace_ids_from_json(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    normalized = normalize_identifier_tuple(
        parsed,
        field_name="notification_workspace_ids",
    )
    return () if normalized is None else normalized


def _notification_receivers_to_json(receivers: tuple[str, ...]) -> str:
    return json.dumps(list(receivers), ensure_ascii=False)


def _notification_receivers_from_json(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("Invalid persisted notification_receivers_json")
    seen: set[str] = set()
    result: list[str] = []
    for item in parsed:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _im_config_to_json(config: XiaolubanImConfig) -> str:
    return config.model_dump_json()


def _im_config_from_json(value: str) -> XiaolubanImConfig:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Invalid persisted im_config_json")
    return XiaolubanImConfig.model_validate(parsed)


__all__ = ["XiaolubanAccountRepository"]
