# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from pydantic import JsonValue, ValidationError

from relay_teams.gateway.discord.models import (
    DiscordAccountRecord,
    DiscordAccountStatus,
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
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class DiscordAccountRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS discord_accounts (
                    account_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    bot_user_id TEXT,
                    application_id TEXT,
                    allowed_channel_ids_json TEXT NOT NULL,
                    allow_channel_messages INTEGER NOT NULL,
                    workspace_id TEXT NOT NULL,
                    session_mode TEXT NOT NULL,
                    normal_root_role_id TEXT,
                    orchestration_preset_id TEXT,
                    yolo INTEGER NOT NULL,
                    thinking_json TEXT NOT NULL,
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
            repository_name="DiscordAccountRepository",
            operation_name="init_tables",
        )

    async def list_accounts(self) -> tuple[DiscordAccountRecord, ...]:
        async def operation(
            conn: aiosqlite.Connection,
        ) -> tuple[DiscordAccountRecord, ...]:
            rows = await async_fetchall(
                conn,
                "SELECT * FROM discord_accounts ORDER BY created_at DESC",
            )
            records: list[DiscordAccountRecord] = []
            for row in rows:
                try:
                    records.append(self._to_record(row))
                except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                    _log_invalid_discord_account_row(row=row, error=exc)
            return tuple(records)

        return await self._run_async_read(operation)

    async def get_account(self, account_id: str) -> DiscordAccountRecord:
        async def operation(conn: aiosqlite.Connection) -> DiscordAccountRecord:
            row = await async_fetchone(
                conn,
                "SELECT * FROM discord_accounts WHERE account_id=?",
                (account_id,),
            )
            if row is None:
                raise KeyError(f"Unknown Discord account_id: {account_id}")
            try:
                return self._to_record(row, fallback_invalid_timestamps=True)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                _log_invalid_discord_account_row(row=row, error=exc)
                raise KeyError(f"Unknown Discord account_id: {account_id}") from exc

        return await self._run_async_read(operation)

    async def upsert_account(
        self,
        record: DiscordAccountRecord,
    ) -> DiscordAccountRecord:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                INSERT INTO discord_accounts(
                    account_id,
                    display_name,
                    status,
                    bot_user_id,
                    application_id,
                    allowed_channel_ids_json,
                    allow_channel_messages,
                    workspace_id,
                    session_mode,
                    normal_root_role_id,
                    orchestration_preset_id,
                    yolo,
                    thinking_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    status=excluded.status,
                    bot_user_id=excluded.bot_user_id,
                    application_id=excluded.application_id,
                    allowed_channel_ids_json=excluded.allowed_channel_ids_json,
                    allow_channel_messages=excluded.allow_channel_messages,
                    workspace_id=excluded.workspace_id,
                    session_mode=excluded.session_mode,
                    normal_root_role_id=excluded.normal_root_role_id,
                    orchestration_preset_id=excluded.orchestration_preset_id,
                    yolo=excluded.yolo,
                    thinking_json=excluded.thinking_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.account_id,
                    record.display_name,
                    record.status.value,
                    record.bot_user_id,
                    record.application_id,
                    json.dumps(tuple(record.allowed_channel_ids), ensure_ascii=False),
                    1 if record.allow_channel_messages else 0,
                    record.workspace_id,
                    record.session_mode.value,
                    record.normal_root_role_id,
                    record.orchestration_preset_id,
                    1 if record.yolo else 0,
                    json.dumps(
                        record.thinking.model_dump(mode="json"), ensure_ascii=False
                    ),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="upsert_account",
            operation=operation,
        )
        return await self.get_account(record.account_id)

    async def delete_account(self, account_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM discord_accounts WHERE account_id=?",
                (account_id,),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_account",
            operation=operation,
        )

    @staticmethod
    def _to_record(
        row: sqlite3.Row,
        *,
        fallback_invalid_timestamps: bool = False,
    ) -> DiscordAccountRecord:
        account_id = require_persisted_identifier(
            row["account_id"],
            field_name="account_id",
        )
        created_at, updated_at = _load_discord_account_timestamps(
            row=row,
            account_id=account_id,
            fallback_invalid_timestamps=fallback_invalid_timestamps,
        )
        allowed_channel_ids = normalize_identifier_tuple(
            json.loads(str(row["allowed_channel_ids_json"])),
            field_name="allowed_channel_ids",
        )
        return DiscordAccountRecord.model_validate(
            {
                "account_id": account_id,
                "display_name": str(row["display_name"]),
                "status": DiscordAccountStatus(str(row["status"])),
                "bot_user_id": normalize_persisted_text(row["bot_user_id"]),
                "application_id": normalize_persisted_text(row["application_id"]),
                "allowed_channel_ids": allowed_channel_ids or (),
                "allow_channel_messages": bool(int(row["allow_channel_messages"])),
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
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )


def _load_discord_account_timestamps(
    *,
    row: sqlite3.Row,
    account_id: str,
    fallback_invalid_timestamps: bool,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if not fallback_invalid_timestamps:
        if created_at is None:
            raise ValueError("Invalid persisted created_at")
        if updated_at is None:
            raise ValueError("Invalid persisted updated_at")
        return created_at, updated_at
    fallback_now = datetime.now(tz=timezone.utc)
    if created_at is None:
        created_at = updated_at or fallback_now
        _log_invalid_discord_account_timestamp(
            account_id=account_id,
            field_name="created_at",
            fallback_iso=created_at.isoformat(),
        )
    if updated_at is None:
        updated_at = created_at
        _log_invalid_discord_account_timestamp(
            account_id=account_id,
            field_name="updated_at",
            fallback_iso=updated_at.isoformat(),
        )
    return created_at, updated_at


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_discord_account_timestamp(
    *,
    account_id: str,
    field_name: str,
    fallback_iso: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "account_id": account_id,
        "field_name": field_name,
        "fallback_iso": fallback_iso,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="gateway.discord.account_repository.timestamp_invalid",
        message="Using fallback for invalid persisted Discord account timestamp",
        payload=payload,
    )


def _log_invalid_discord_account_row(*, row: sqlite3.Row, error: Exception) -> None:
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
        event="gateway.discord.account_repository.row_invalid",
        message="Skipping invalid persisted Discord account row",
        payload=payload,
    )
