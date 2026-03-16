# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_teams.persistence.db import open_sqlite
from agent_teams.roles.memory_models import RoleMemoryRecord


class RoleMemoryRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._ensure_role_memories_schema()
        self._drop_legacy_daily_table()
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

    def _ensure_role_memories_schema(self) -> None:
        columns = self._table_info("role_memories")
        if not columns:
            self._create_role_memories_table()
            return
        if self._has_role_memories_schema(columns):
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
                PRIMARY KEY (role_id, workspace_id)
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
