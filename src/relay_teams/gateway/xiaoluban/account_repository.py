# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Dict, List, Tuple

from pydantic import JsonValue, ValidationError

from relay_teams.gateway.xiaoluban.models import (
    XiaolubanAccountRecord,
    XiaolubanAccountStatus,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.validation import (
    normalize_identifier_tuple,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class XiaolubanAccountRepository:
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
                CREATE TABLE IF NOT EXISTS xiaoluban_accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    derived_uid TEXT NOT NULL,
                    notification_workspace_ids_json TEXT NOT NULL DEFAULT '[]',
                    notification_receiver TEXT,
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

    def list_accounts(self) -> Tuple[XiaolubanAccountRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM xiaoluban_accounts ORDER BY created_at DESC"
        ).fetchall()
        records: List[XiaolubanAccountRecord] = []
        for row in rows:
            try:
                records.append(self._to_record(row))
            except (ValidationError, ValueError) as exc:
                _log_invalid_row(row=row, error=exc)
        return tuple(records)

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
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    base_url=excluded.base_url,
                    status=excluded.status,
                    derived_uid=excluded.derived_uid,
                    notification_workspace_ids_json=excluded.notification_workspace_ids_json,
                    notification_receiver=excluded.notification_receiver,
                    updated_at=excluded.updated_at
                """,
                (
                    record.account_id,
                    record.display_name,
                    record.base_url,
                    record.status.value,
                    record.derived_uid,
                    _workspace_ids_to_json(record.notification_workspace_ids),
                    record.notification_receiver,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            ),
            lock=self._lock,
            repository_name="XiaolubanAccountRepository",
            operation_name="upsert_account",
        )
        return self.get_account(record.account_id)

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
        return XiaolubanAccountRecord(
            account_id=account_id,
            display_name=str(row["display_name"]),
            base_url=str(row["base_url"]),
            status=XiaolubanAccountStatus(str(row["status"])),
            derived_uid=derived_uid,
            notification_workspace_ids=_workspace_ids_from_json(
                str(row["notification_workspace_ids_json"] or "[]")
            ),
            notification_receiver=(
                str(row["notification_receiver"])
                if row["notification_receiver"] is not None
                else None
            ),
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
    payload: Dict[str, JsonValue] = {
        "account_id": _persisted_value_preview(row["account_id"]),
        "derived_uid": _persisted_value_preview(row["derived_uid"]),
        "notification_workspace_ids_json": _persisted_value_preview(
            row["notification_workspace_ids_json"]
        ),
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


__all__ = ["XiaolubanAccountRepository"]
