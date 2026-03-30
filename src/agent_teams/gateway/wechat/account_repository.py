# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, ValidationError

from agent_teams.logger import get_logger, log_event
from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.gateway.wechat.models import (
    WeChatAccountRecord,
    WeChatAccountStatus,
)
from agent_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class WeChatAccountRepository:
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
                CREATE TABLE IF NOT EXISTS wechat_accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    cdn_base_url TEXT NOT NULL,
                    route_tag TEXT,
                    status TEXT NOT NULL,
                    remote_user_id TEXT,
                    sync_cursor TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    normal_root_role_id TEXT,
                    orchestration_preset_id TEXT,
                    yolo INTEGER NOT NULL,
                    thinking_json TEXT NOT NULL,
                    last_login_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="WeChatAccountRepository",
            operation_name="init_tables",
        )

    def list_accounts(self) -> tuple[WeChatAccountRecord, ...]:
        rows = self._conn.execute(
            "SELECT * FROM wechat_accounts ORDER BY created_at DESC"
        ).fetchall()
        records: list[WeChatAccountRecord] = []
        for row in rows:
            try:
                records.append(self._to_record(row))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                _log_invalid_wechat_account_row(row=row, error=exc)
        return tuple(records)

    def get_account(self, account_id: str) -> WeChatAccountRecord:
        row = self._conn.execute(
            "SELECT * FROM wechat_accounts WHERE account_id=?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown account_id: {account_id}")
        try:
            return self._to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_wechat_account_row(row=row, error=exc)
            raise KeyError(f"Unknown account_id: {account_id}") from exc

    def upsert_account(self, record: WeChatAccountRecord) -> WeChatAccountRecord:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO wechat_accounts(
                    account_id,
                    display_name,
                    base_url,
                    cdn_base_url,
                    route_tag,
                    status,
                    remote_user_id,
                    sync_cursor,
                    workspace_id,
                    session_mode,
                    normal_root_role_id,
                    orchestration_preset_id,
                    yolo,
                    thinking_json,
                    last_login_at,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    base_url=excluded.base_url,
                    cdn_base_url=excluded.cdn_base_url,
                    route_tag=excluded.route_tag,
                    status=excluded.status,
                    remote_user_id=excluded.remote_user_id,
                    sync_cursor=excluded.sync_cursor,
                    workspace_id=excluded.workspace_id,
                    session_mode=excluded.session_mode,
                    normal_root_role_id=excluded.normal_root_role_id,
                    orchestration_preset_id=excluded.orchestration_preset_id,
                    yolo=excluded.yolo,
                    thinking_json=excluded.thinking_json,
                    last_login_at=excluded.last_login_at,
                    updated_at=excluded.updated_at
                """,
                (
                    record.account_id,
                    record.display_name,
                    record.base_url,
                    record.cdn_base_url,
                    record.route_tag,
                    record.status.value,
                    record.remote_user_id,
                    record.sync_cursor,
                    record.workspace_id,
                    record.session_mode.value,
                    record.normal_root_role_id,
                    record.orchestration_preset_id,
                    1 if record.yolo else 0,
                    json.dumps(
                        record.thinking.model_dump(mode="json"), ensure_ascii=False
                    ),
                    record.last_login_at.isoformat()
                    if record.last_login_at is not None
                    else None,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            ),
            lock=self._lock,
            repository_name="WeChatAccountRepository",
            operation_name="upsert_account",
        )
        return self.get_account(record.account_id)

    def delete_account(self, account_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM wechat_accounts WHERE account_id=?",
                (account_id,),
            ),
            lock=self._lock,
            repository_name="WeChatAccountRepository",
            operation_name="delete_account",
        )

    def _to_record(self, row: sqlite3.Row) -> WeChatAccountRecord:
        return WeChatAccountRecord.model_validate(
            {
                "account_id": require_persisted_identifier(
                    row["account_id"],
                    field_name="account_id",
                ),
                "display_name": str(row["display_name"]),
                "base_url": str(row["base_url"]),
                "cdn_base_url": str(row["cdn_base_url"]),
                "route_tag": _normalize_optional_persisted_text(row["route_tag"]),
                "status": WeChatAccountStatus(str(row["status"])),
                "remote_user_id": normalize_persisted_text(row["remote_user_id"]),
                "sync_cursor": str(row["sync_cursor"]),
                "workspace_id": require_persisted_identifier(
                    row["workspace_id"],
                    field_name="workspace_id",
                ),
                "session_mode": str(row["session_mode"]),
                "normal_root_role_id": normalize_persisted_text(
                    row["normal_root_role_id"]
                ),
                "orchestration_preset_id": normalize_persisted_text(
                    row["orchestration_preset_id"]
                ),
                "yolo": bool(int(row["yolo"])),
                "thinking": json.loads(str(row["thinking_json"])),
                "last_login_at": (
                    _optional_wechat_account_timestamp(
                        row=row,
                        account_id=str(row["account_id"]),
                        field_name="last_login_at",
                    )
                    if normalize_persisted_text(row["last_login_at"]) is not None
                    else None
                ),
                "created_at": _require_wechat_account_timestamp(
                    row=row,
                    account_id=str(row["account_id"]),
                    field_name="created_at",
                ),
                "updated_at": _require_wechat_account_timestamp(
                    row=row,
                    account_id=str(row["account_id"]),
                    field_name="updated_at",
                ),
            }
        )

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(tz=timezone.utc)


def _require_wechat_account_timestamp(
    *,
    row: sqlite3.Row,
    account_id: str,
    field_name: str,
) -> datetime:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_wechat_account_timestamp(
        account_id=account_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _optional_wechat_account_timestamp(
    *,
    row: sqlite3.Row,
    account_id: str,
    field_name: str,
) -> datetime | None:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_wechat_account_timestamp(
        account_id=account_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _normalize_optional_persisted_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _log_invalid_wechat_account_timestamp(
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
        event="gateway.wechat.account_repository.timestamp_invalid",
        message="Invalid persisted WeChat account timestamp",
        payload=payload,
    )


def _log_invalid_wechat_account_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "account_id": _persisted_value_preview(row["account_id"]),
        "workspace_id": _persisted_value_preview(row["workspace_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.wechat.account_repository.row_invalid",
        message="Skipping invalid persisted WeChat account row",
        payload=payload,
    )
