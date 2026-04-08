# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, ValidationError

from relay_teams.gateway.feishu.models import (
    FEISHU_PLATFORM,
    FeishuGatewayAccountRecord,
    FeishuGatewayAccountStatus,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class FeishuAccountNameConflictError(ValueError):
    pass


class FeishuAccountRepository:
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
                CREATE TABLE IF NOT EXISTS feishu_gateway_accounts (
                    account_id          TEXT PRIMARY KEY,
                    name                TEXT NOT NULL UNIQUE,
                    display_name        TEXT NOT NULL,
                    status              TEXT NOT NULL,
                    source_config_json  TEXT NOT NULL,
                    target_config_json  TEXT,
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_feishu_gateway_accounts_status
                ON feishu_gateway_accounts(status, updated_at DESC)
                """
            )
            self._migrate_legacy_triggers()

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="FeishuAccountRepository",
            operation_name="init_tables",
        )

    def _migrate_legacy_triggers(self) -> None:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='triggers'"
        ).fetchall()
        if not rows:
            return
        existing = self._conn.execute(
            "SELECT COUNT(*) AS count FROM feishu_gateway_accounts"
        ).fetchone()
        if existing is not None and int(existing["count"]) > 0:
            return
        legacy_rows = self._conn.execute(
            """
            SELECT trigger_id, name, display_name, status, source_config_json,
                   target_config_json, created_at, updated_at
            FROM triggers
            WHERE source_type=?
            ORDER BY created_at DESC
            """,
            ("im",),
        ).fetchall()
        for row in legacy_rows:
            source_config = _load_json_object(row["source_config_json"])
            provider = str(source_config.get("provider", "")).strip().lower()
            if provider != FEISHU_PLATFORM:
                continue
            self._conn.execute(
                """
                INSERT OR IGNORE INTO feishu_gateway_accounts(
                    account_id,
                    name,
                    display_name,
                    status,
                    source_config_json,
                    target_config_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(row["trigger_id"]),
                    str(row["name"]),
                    str(row["display_name"]),
                    str(row["status"]),
                    json.dumps(source_config),
                    row["target_config_json"],
                    str(row["created_at"]),
                    str(row["updated_at"]),
                ),
            )

    def create_account(
        self,
        record: FeishuGatewayAccountRecord,
    ) -> FeishuGatewayAccountRecord:
        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO feishu_gateway_accounts(
                        account_id,
                        name,
                        display_name,
                        status,
                        source_config_json,
                        target_config_json,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.account_id,
                        record.name,
                        record.display_name,
                        record.status.value,
                        json.dumps(record.source_config),
                        json.dumps(record.target_config)
                        if record.target_config is not None
                        else None,
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                ),
                lock=self._lock,
                repository_name="FeishuAccountRepository",
                operation_name="create_account",
            )
        except sqlite3.IntegrityError as exc:
            if "name" in str(exc).lower():
                raise FeishuAccountNameConflictError(
                    f"Feishu account name already exists: {record.name}"
                ) from exc
            raise
        return record

    def update_account(
        self,
        record: FeishuGatewayAccountRecord,
    ) -> FeishuGatewayAccountRecord:
        try:
            run_sqlite_write_with_retry(
                conn=self._conn,
                db_path=self._db_path,
                operation=lambda: self._conn.execute(
                    """
                    UPDATE feishu_gateway_accounts
                    SET name=?,
                        display_name=?,
                        status=?,
                        source_config_json=?,
                        target_config_json=?,
                        updated_at=?
                    WHERE account_id=?
                    """,
                    (
                        record.name,
                        record.display_name,
                        record.status.value,
                        json.dumps(record.source_config),
                        json.dumps(record.target_config)
                        if record.target_config is not None
                        else None,
                        record.updated_at.isoformat(),
                        record.account_id,
                    ),
                ),
                lock=self._lock,
                repository_name="FeishuAccountRepository",
                operation_name="update_account",
            )
        except sqlite3.IntegrityError as exc:
            if "name" in str(exc).lower():
                raise FeishuAccountNameConflictError(
                    f"Feishu account name already exists: {record.name}"
                ) from exc
            raise
        return record

    def get_account(self, account_id: str) -> FeishuGatewayAccountRecord:
        row = self._conn.execute(
            """
            SELECT *
            FROM feishu_gateway_accounts
            WHERE account_id=?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown Feishu account: {account_id}")
        try:
            return self._row_to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_feishu_account_row(row=row, error=exc)
            raise KeyError(f"Unknown Feishu account: {account_id}") from exc

    def list_accounts(self) -> tuple[FeishuGatewayAccountRecord, ...]:
        rows = self._conn.execute(
            """
            SELECT *
            FROM feishu_gateway_accounts
            ORDER BY created_at DESC
            """
        ).fetchall()
        records: list[FeishuGatewayAccountRecord] = []
        for row in rows:
            try:
                records.append(self._row_to_record(row))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                _log_invalid_feishu_account_row(row=row, error=exc)
        return tuple(records)

    def delete_account(self, account_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM feishu_gateway_accounts WHERE account_id=?",
                (account_id,),
            ),
            lock=self._lock,
            repository_name="FeishuAccountRepository",
            operation_name="delete_account",
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> FeishuGatewayAccountRecord:
        return FeishuGatewayAccountRecord(
            account_id=require_persisted_identifier(
                row["account_id"],
                field_name="account_id",
            ),
            name=require_persisted_identifier(row["name"], field_name="name"),
            display_name=str(row["display_name"]),
            status=FeishuGatewayAccountStatus(str(row["status"])),
            source_config=_load_json_object(row["source_config_json"]),
            target_config=(
                _load_json_object(row["target_config_json"])
                if normalize_persisted_text(row["target_config_json"]) is not None
                else None
            ),
            created_at=_require_feishu_account_timestamp(
                row=row,
                account_id=str(row["account_id"]),
                field_name="created_at",
            ).astimezone(UTC),
            updated_at=_require_feishu_account_timestamp(
                row=row,
                account_id=str(row["account_id"]),
                field_name="updated_at",
            ).astimezone(UTC),
        )


def _load_json_object(raw_value: object) -> dict[str, JsonValue]:
    normalized = normalize_persisted_text(raw_value)
    if normalized is None:
        return {}
    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        return {}
    return {str(key): value for key, value in parsed.items()}


def _require_feishu_account_timestamp(
    *,
    row: sqlite3.Row,
    account_id: str,
    field_name: str,
) -> datetime:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_feishu_account_timestamp(
        account_id=account_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_feishu_account_timestamp(
    *,
    account_id: str,
    field_name: str,
    raw_preview: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "account_id": account_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.feishu.account_repository.timestamp_invalid",
        message="Invalid persisted Feishu account timestamp",
        payload=payload,
    )


def _log_invalid_feishu_account_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "account_id": _persisted_value_preview(row["account_id"]),
        "name": _persisted_value_preview(row["name"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.feishu.account_repository.row_invalid",
        message="Skipping invalid persisted Feishu account row",
        payload=payload,
    )


__all__ = ["FeishuAccountNameConflictError", "FeishuAccountRepository"]
