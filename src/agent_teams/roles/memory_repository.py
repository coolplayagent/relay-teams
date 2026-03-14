# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.persistence.db import open_sqlite
from agent_teams.roles.memory_models import (
    MemoryKind,
    RoleDailyMemoryRecord,
    RoleMemoryRecord,
)


class RoleMemoryRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._ensure_role_memories_schema()
        self._ensure_role_daily_memories_schema()
        self._conn.commit()

    def read_role_memory(self, *, role_id: str, workspace_id: str) -> RoleMemoryRecord:
        row = self._conn.execute(
            "SELECT * FROM role_memories WHERE role_id=? AND workspace_id=?",
            (role_id, workspace_id),
        ).fetchone()
        if row is None:
            return RoleMemoryRecord(
                role_id=role_id,
                workspace_id=workspace_id,
                content_markdown="",
            )
        return RoleMemoryRecord(
            role_id=str(row["role_id"]),
            workspace_id=str(row["workspace_id"]),
            content_markdown=str(row["content_markdown"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def write_role_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        content_markdown: str,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO role_memories(role_id, workspace_id, content_markdown, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(role_id, workspace_id)
            DO UPDATE SET content_markdown=excluded.content_markdown,
                          updated_at=excluded.updated_at
            """,
            (role_id, workspace_id, content_markdown, now),
        )
        self._conn.commit()

    def read_daily_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        memory_date: str,
        kind: MemoryKind,
    ) -> RoleDailyMemoryRecord:
        row = self._conn.execute(
            """
            SELECT * FROM role_daily_memories
            WHERE role_id=? AND workspace_id=? AND memory_date=? AND kind=?
            """,
            (role_id, workspace_id, memory_date, kind.value),
        ).fetchone()
        if row is None:
            return RoleDailyMemoryRecord(
                role_id=role_id,
                workspace_id=workspace_id,
                memory_date=memory_date,
                kind=kind,
                content_markdown="",
            )
        return RoleDailyMemoryRecord(
            role_id=str(row["role_id"]),
            workspace_id=str(row["workspace_id"]),
            memory_date=str(row["memory_date"]),
            kind=MemoryKind(str(row["kind"])),
            content_markdown=str(row["content_markdown"]),
            source_session_id=(
                str(row["source_session_id"]) if row["source_session_id"] else None
            ),
            source_task_id=str(row["source_task_id"])
            if row["source_task_id"]
            else None,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )

    def write_daily_memory(
        self,
        *,
        role_id: str,
        workspace_id: str,
        memory_date: str,
        kind: MemoryKind,
        content_markdown: str,
        source_session_id: str | None,
        source_task_id: str | None,
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO role_daily_memories(
                role_id, workspace_id, memory_date, kind, content_markdown,
                source_session_id, source_task_id, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(role_id, workspace_id, memory_date, kind)
            DO UPDATE SET
                content_markdown=excluded.content_markdown,
                source_session_id=excluded.source_session_id,
                source_task_id=excluded.source_task_id,
                updated_at=excluded.updated_at
            """,
            (
                role_id,
                workspace_id,
                memory_date,
                kind.value,
                content_markdown,
                source_session_id,
                source_task_id,
                now,
                now,
            ),
        )
        self._conn.commit()

    def _ensure_role_memories_schema(self) -> None:
        columns = self._table_info("role_memories")
        if not columns:
            self._create_role_memories_table()
            return
        if self._has_role_memories_schema(columns):
            return

        self._conn.execute("DROP TABLE role_memories")
        self._create_role_memories_table()

    def _ensure_role_daily_memories_schema(self) -> None:
        columns = self._table_info("role_daily_memories")
        if not columns:
            self._create_role_daily_memories_table()
            return
        if self._has_role_daily_memories_schema(columns):
            return

        self._conn.execute("DROP TABLE role_daily_memories")
        self._create_role_daily_memories_table()

    def _create_role_memories_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_memories (
                role_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                content_markdown TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (role_id, workspace_id)
            )
            """
        )

    def _create_role_daily_memories_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS role_daily_memories (
                role_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                memory_date TEXT NOT NULL,
                kind TEXT NOT NULL,
                content_markdown TEXT NOT NULL,
                source_session_id TEXT,
                source_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (role_id, workspace_id, memory_date, kind)
            )
            """
        )

    def _table_info(self, table_name: str) -> list[sqlite3.Row]:
        return self._conn.execute(f"PRAGMA table_info({table_name})").fetchall()

    def _has_role_memories_schema(self, columns: list[sqlite3.Row]) -> bool:
        column_names = [str(column["name"]) for column in columns]
        pk_columns = [
            str(column["name"])
            for column in sorted(columns, key=lambda column: int(column["pk"]))
            if int(column["pk"]) > 0
        ]
        return column_names == [
            "role_id",
            "workspace_id",
            "content_markdown",
            "updated_at",
        ] and pk_columns == ["role_id", "workspace_id"]

    def _has_role_daily_memories_schema(self, columns: list[sqlite3.Row]) -> bool:
        column_names = [str(column["name"]) for column in columns]
        pk_columns = [
            str(column["name"])
            for column in sorted(columns, key=lambda column: int(column["pk"]))
            if int(column["pk"]) > 0
        ]
        return column_names == [
            "role_id",
            "workspace_id",
            "memory_date",
            "kind",
            "content_markdown",
            "source_session_id",
            "source_task_id",
            "created_at",
            "updated_at",
        ] and pk_columns == ["role_id", "workspace_id", "memory_date", "kind"]
