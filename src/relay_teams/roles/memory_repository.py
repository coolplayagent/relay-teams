# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from relay_teams.logger import get_logger
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchone,
)
from relay_teams.roles.memory_models import (
    RoleAssessmentState,
    RoleMemoryRecord,
    RolePerformanceMetrics,
)

LOGGER = get_logger(__name__)


class RoleMemoryRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._ensure_role_memories_schema()
            self._drop_legacy_daily_table()

        self._run_write(operation_name="init_tables", operation=operation)

    def read_role_memory(self, *, role_id: str, workspace_id: str) -> RoleMemoryRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT * FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            ).fetchone()
        )
        if row is None:
            return RoleMemoryRecord(
                role_id=role_id,
                workspace_id=workspace_id,
                content_markdown="",
                updated_at=None,
            )
        return self._row_to_record(row)

    async def read_role_memory_async(
        self, *, role_id: str, workspace_id: str
    ) -> RoleMemoryRecord:
        async def operation(conn: aiosqlite.Connection) -> RoleMemoryRecord:
            row = await async_fetchone(
                conn,
                "SELECT * FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            )
            if row is None:
                return RoleMemoryRecord(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    content_markdown="",
                    updated_at=None,
                )
            return self._row_to_record(row)

        return await self._run_async_read(operation)

    def write_role_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        content_markdown: str,
        performance: RolePerformanceMetrics | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        performance_json_str = self._performance_to_json(performance)
        self._run_write(
            operation_name="write_role_memory",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO role_memories(
                    role_id, workspace_id, content_markdown, updated_at,
                    performance_json
                )
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(role_id, workspace_id)
                DO UPDATE SET content_markdown=excluded.content_markdown,
                              updated_at=excluded.updated_at,
                              performance_json=excluded.performance_json
                """,
                (
                    role_id,
                    workspace_id,
                    content_markdown,
                    now,
                    performance_json_str,
                ),
            ),
        )

    async def write_role_memory_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        content_markdown: str,
        performance: RolePerformanceMetrics | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        performance_json_str = self._performance_to_json(performance)

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                INSERT INTO role_memories(
                    role_id, workspace_id, content_markdown, updated_at,
                    performance_json
                )
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(role_id, workspace_id)
                DO UPDATE SET content_markdown=excluded.content_markdown,
                              updated_at=excluded.updated_at,
                              performance_json=excluded.performance_json
                """,
                (
                    role_id,
                    workspace_id,
                    content_markdown,
                    now,
                    performance_json_str,
                ),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="write_role_memory_async",
            operation=operation,
        )

    def read_assessment_state(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RoleAssessmentState | None:
        row = self._run_read(
            lambda: self._conn.execute(
                "SELECT assessment_state_json FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            ).fetchone()
        )
        if row is None:
            return None
        return self._parse_assessment_state(row["assessment_state_json"])

    async def read_assessment_state_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
    ) -> RoleAssessmentState | None:
        async def operation(conn: aiosqlite.Connection) -> RoleAssessmentState | None:
            row = await async_fetchone(
                conn,
                "SELECT assessment_state_json FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            )
            if row is None:
                return None
            return self._parse_assessment_state(row["assessment_state_json"])

        return await self._run_async_read(operation)

    def write_assessment_state(
        self,
        *,
        role_id: str,
        workspace_id: str,
        state: RoleAssessmentState,
    ) -> None:
        state_json_str = state.model_dump_json()
        self._run_write(
            operation_name="write_assessment_state",
            operation=lambda: self._conn.execute(
                """
                UPDATE role_memories SET assessment_state_json=?
                WHERE role_id=? AND workspace_id=?
                """,
                (state_json_str, role_id, workspace_id),
            ),
        )

    async def write_assessment_state_async(
        self,
        *,
        role_id: str,
        workspace_id: str,
        state: RoleAssessmentState,
    ) -> None:
        state_json_str = state.model_dump_json()

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                UPDATE role_memories SET assessment_state_json=?
                WHERE role_id=? AND workspace_id=?
                """,
                (state_json_str, role_id, workspace_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="write_assessment_state_async",
            operation=operation,
        )

    def delete_role_memory(self, *, role_id: str, workspace_id: str) -> None:
        self._run_write(
            operation_name="delete_role_memory",
            operation=lambda: self._conn.execute(
                "DELETE FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            ),
        )

    async def delete_role_memory_async(
        self, *, role_id: str, workspace_id: str
    ) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM role_memories WHERE role_id=? AND workspace_id=?",
                (role_id, workspace_id),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="delete_role_memory_async",
            operation=operation,
        )

    # ------------------------------------------------------------------
    # schema management
    # ------------------------------------------------------------------

    def _ensure_role_memories_schema(self) -> None:
        columns = self._table_info("role_memories")
        if not columns:
            self._create_role_memories_table()
            return
        if self._has_role_memories_schema(columns):
            self._migrate_role_memories_schema(columns)
            return

        self._conn.execute("DROP TABLE role_memories")
        self._create_role_memories_table()

    def _drop_legacy_daily_table(self) -> None:
        self._conn.execute("DROP TABLE IF EXISTS role_daily_memories")

    def _create_role_memories_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_memories (
                role_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                content_markdown TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                performance_json TEXT,
                assessment_state_json TEXT,
                PRIMARY KEY (role_id, workspace_id)
            )
            """
        )

    def _migrate_role_memories_schema(self, columns: list[sqlite3.Row]) -> None:
        column_names = {str(column["name"]) for column in columns}
        if "performance_json" not in column_names:
            LOGGER.warning("Migrating role_memories: adding performance_json column")
            self._conn.execute(
                "ALTER TABLE role_memories ADD COLUMN performance_json TEXT"
            )
        if "assessment_state_json" not in column_names:
            LOGGER.warning(
                "Migrating role_memories: adding assessment_state_json column"
            )
            self._conn.execute(
                "ALTER TABLE role_memories ADD COLUMN assessment_state_json TEXT"
            )

    def _table_info(self, table_name: str) -> list[sqlite3.Row]:
        return self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()

    @staticmethod
    def _has_role_memories_schema(columns: list[sqlite3.Row]) -> bool:
        column_names = {str(column["name"]) for column in columns}
        pk_columns = [
            str(column["name"])
            for column in sorted(columns, key=lambda column: int(column["pk"]))
            if int(column["pk"]) > 0
        ]
        required_columns = {"role_id", "workspace_id", "content_markdown", "updated_at"}
        return required_columns.issubset(column_names) and pk_columns == [
            "role_id",
            "workspace_id",
        ]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _row_to_record(self, row: sqlite3.Row | aiosqlite.Row) -> RoleMemoryRecord:
        role_id = str(row["role_id"])
        workspace_id = str(row["workspace_id"])
        updated_at_raw = row["updated_at"]
        updated_at = (
            datetime.fromisoformat(str(updated_at_raw)) if updated_at_raw else None
        )
        performance = self._parse_performance(
            role_id=role_id,
            workspace_id=workspace_id,
            performance_json=(
                row["performance_json"] if "performance_json" in row.keys() else None
            ),
        )
        return RoleMemoryRecord(
            role_id=role_id,
            workspace_id=workspace_id,
            content_markdown=str(row["content_markdown"]),
            updated_at=updated_at,
            performance=performance,
        )

    @staticmethod
    def _performance_to_json(performance: RolePerformanceMetrics | None) -> str | None:
        if performance is None:
            return None
        return performance.model_dump_json()

    @staticmethod
    def _parse_performance(
        *,
        role_id: str,
        workspace_id: str,
        performance_json: str | None,
    ) -> RolePerformanceMetrics | None:
        if not performance_json:
            return None
        try:
            data = json.loads(performance_json)
            return RolePerformanceMetrics(**data)
        except (json.JSONDecodeError, Exception) as exc:
            LOGGER.warning(
                "Failed to parse performance_json for role_id=%s workspace_id=%s: %s",
                role_id,
                workspace_id,
                exc,
            )
            return None

    @staticmethod
    def _parse_assessment_state(
        assessment_state_json: str | None,
    ) -> RoleAssessmentState | None:
        if not assessment_state_json:
            return None
        try:
            data = json.loads(assessment_state_json)
            return RoleAssessmentState(**data)
        except (json.JSONDecodeError, Exception) as exc:
            LOGGER.warning(
                "Failed to parse assessment_state_json: %s",
                exc,
            )
            return None
