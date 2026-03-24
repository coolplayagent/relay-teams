# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from agent_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from agent_teams.workspace.workspace_models import (
    WorkspaceProfile,
    WorkspaceRecord,
    default_workspace_profile,
)


class WorkspaceRepository:
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
                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    root_path TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    profile_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            columns = [
                str(row["name"])
                for row in self._conn.execute(
                    "PRAGMA table_info(workspaces)"
                ).fetchall()
            ]
            if "profile_json" not in columns:
                self._conn.execute(
                    "ALTER TABLE workspaces ADD COLUMN profile_json TEXT NOT NULL DEFAULT '{}'"
                )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="WorkspaceRepository",
            operation_name="init_tables",
        )

    def create(
        self,
        *,
        workspace_id: str,
        root_path: Path,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        now = datetime.now(tz=timezone.utc).isoformat()
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            root_path=root_path.resolve(),
            profile=profile or default_workspace_profile(),
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                """
                INSERT INTO workspaces(workspace_id, root_path, backend, profile_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    record.workspace_id,
                    str(record.root_path),
                    record.profile.backend.value,
                    json.dumps(
                        record.profile.model_dump(mode="json"), ensure_ascii=False
                    ),
                    now,
                    now,
                ),
            ),
            lock=self._lock,
            repository_name="WorkspaceRepository",
            operation_name="create",
        )
        return record

    def get(self, workspace_id: str) -> WorkspaceRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown workspace_id: {workspace_id}")
        return self._to_record(row)

    def list_all(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workspaces ORDER BY created_at DESC"
            ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete(self, workspace_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            ),
            lock=self._lock,
            repository_name="WorkspaceRepository",
            operation_name="delete",
        )

    def exists(self, workspace_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        return row is not None

    def _to_record(self, row: sqlite3.Row) -> WorkspaceRecord:
        profile_raw = str(row["profile_json"] or "{}")
        loaded = json.loads(profile_raw)
        profile = (
            WorkspaceProfile.model_validate(loaded)
            if isinstance(loaded, dict) and loaded
            else default_workspace_profile()
        )
        return WorkspaceRecord(
            workspace_id=str(row["workspace_id"]),
            root_path=Path(str(row["root_path"])).resolve(),
            profile=profile,
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )
