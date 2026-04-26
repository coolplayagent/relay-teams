# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import run_sqlite_write_with_retry
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.tools.workspace_tools.shell_policy import ShellRuntimeFamily
from relay_teams.validation import (
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class ShellApprovalScope(str, Enum):
    EXACT = "exact"
    PREFIX = "prefix"


class ShellApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_key: str = Field(min_length=1)
    runtime_family: ShellRuntimeFamily
    scope: ShellApprovalScope
    value: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ShellApprovalRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shell_approval_grants (
                    workspace_key   TEXT NOT NULL,
                    runtime_family  TEXT NOT NULL,
                    scope           TEXT NOT NULL,
                    value           TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    PRIMARY KEY(workspace_key, runtime_family, scope, value)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shell_approval_grants_lookup "
                "ON shell_approval_grants(workspace_key, runtime_family, scope)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ShellApprovalRepository",
            operation_name="init_tables",
        )

    def grant(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        scope: ShellApprovalScope,
        value: str,
    ) -> ShellApprovalGrant:
        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError("shell approval value must not be empty")
        now = datetime.now(timezone.utc).isoformat()

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO shell_approval_grants(
                    workspace_key, runtime_family, scope, value, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_key, runtime_family, scope, value)
                DO UPDATE SET updated_at=excluded.updated_at
                """,
                (
                    workspace_key,
                    runtime_family.value,
                    scope.value,
                    normalized_value,
                    now,
                    now,
                ),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="ShellApprovalRepository",
            operation_name="grant",
        )
        record = self.get(
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            scope=scope,
            value=normalized_value,
        )
        if record is None:
            raise RuntimeError("Failed to persist shell approval grant")
        return record

    async def grant_async(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        scope: ShellApprovalScope,
        value: str,
    ) -> ShellApprovalGrant:
        return await self._call_sync_async(
            self.grant,
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            scope=scope,
            value=value,
        )

    def get(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        scope: ShellApprovalScope,
        value: str,
    ) -> ShellApprovalGrant | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM shell_approval_grants
                WHERE workspace_key=? AND runtime_family=? AND scope=? AND value=?
                """,
                (
                    workspace_key,
                    runtime_family.value,
                    scope.value,
                    value,
                ),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    async def get_async(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        scope: ShellApprovalScope,
        value: str,
    ) -> ShellApprovalGrant | None:
        return await self._call_sync_async(
            self.get,
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            scope=scope,
            value=value,
        )

    def has_exact_grant(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        normalized_command: str,
    ) -> bool:
        return (
            self.get(
                workspace_key=workspace_key,
                runtime_family=runtime_family,
                scope=ShellApprovalScope.EXACT,
                value=normalized_command,
            )
            is not None
        )

    async def has_exact_grant_async(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        normalized_command: str,
    ) -> bool:
        return await self._call_sync_async(
            self.has_exact_grant,
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            normalized_command=normalized_command,
        )

    def has_prefix_grants(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        prefix_candidates: tuple[str, ...],
    ) -> bool:
        if not prefix_candidates:
            return False
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT value FROM shell_approval_grants
                WHERE workspace_key=? AND runtime_family=? AND scope=?
                """,
                (
                    workspace_key,
                    runtime_family.value,
                    ShellApprovalScope.PREFIX.value,
                ),
            ).fetchall()
        granted_values = {str(row["value"]) for row in rows}
        return all(candidate in granted_values for candidate in prefix_candidates)

    async def has_prefix_grants_async(
        self,
        *,
        workspace_key: str,
        runtime_family: ShellRuntimeFamily,
        prefix_candidates: tuple[str, ...],
    ) -> bool:
        return await self._call_sync_async(
            self.has_prefix_grants,
            workspace_key=workspace_key,
            runtime_family=runtime_family,
            prefix_candidates=prefix_candidates,
        )

    def _row_to_record(self, row: sqlite3.Row) -> ShellApprovalGrant:
        created_at = parse_persisted_datetime_or_none(row["created_at"])
        updated_at = parse_persisted_datetime_or_none(row["updated_at"])
        if created_at is None or updated_at is None:
            payload: dict[str, JsonValue] = {
                "workspace_key": str(row["workspace_key"]),
                "runtime_family": str(row["runtime_family"]),
                "scope": str(row["scope"]),
                "value": str(row["value"]),
            }
            log_event(
                LOGGER,
                logging.WARNING,
                event="tools.shell_approval_repo.invalid_timestamp",
                message="Skipping invalid shell approval row timestamps",
                payload=payload,
            )
            raise ValueError("Invalid persisted shell approval timestamps")
        return ShellApprovalGrant(
            workspace_key=require_persisted_identifier(
                row["workspace_key"],
                field_name="workspace_key",
            ),
            runtime_family=ShellRuntimeFamily(str(row["runtime_family"])),
            scope=ShellApprovalScope(str(row["scope"])),
            value=require_persisted_identifier(row["value"], field_name="value"),
            created_at=created_at,
            updated_at=updated_at,
        )
