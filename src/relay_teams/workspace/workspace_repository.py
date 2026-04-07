# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from pydantic import JsonValue, ValidationError

from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.validation import (
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)
from relay_teams.workspace.workspace_models import (
    WorkspaceProfile,
    WorkspaceRecord,
    default_workspace_profile,
)

LOGGER = get_logger(__name__)


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
        try:
            return self._to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_workspace_row(row=row, error=exc)
            raise KeyError(f"Unknown workspace_id: {workspace_id}") from exc

    def list_all(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workspaces ORDER BY created_at DESC"
            ).fetchall()
        records: list[WorkspaceRecord] = []
        for row in rows:
            try:
                records.append(self._to_record(row))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                _log_invalid_workspace_row(row=row, error=exc)
        return tuple(records)

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
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            root_path=Path(str(row["root_path"])).resolve(),
            profile=profile,
            created_at=_require_workspace_timestamp(
                row=row,
                workspace_id=str(row["workspace_id"]),
                field_name="created_at",
            ),
            updated_at=_require_workspace_timestamp(
                row=row,
                workspace_id=str(row["workspace_id"]),
                field_name="updated_at",
            ),
        )


def _require_workspace_timestamp(
    *,
    row: sqlite3.Row,
    workspace_id: str,
    field_name: str,
) -> datetime:
    parsed = parse_persisted_datetime_or_none(row[field_name])
    if parsed is not None:
        return parsed
    _log_invalid_workspace_timestamp(
        workspace_id=workspace_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(row[field_name]),
    )
    raise ValueError(f"Invalid persisted {field_name}")


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_workspace_timestamp(
    *,
    workspace_id: str,
    field_name: str,
    raw_preview: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "workspace_id": workspace_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="workspace.repository.timestamp_invalid",
        message="Invalid persisted workspace timestamp",
        payload=payload,
    )


def _log_invalid_workspace_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "workspace_id": _persisted_value_preview(row["workspace_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="workspace.repository.row_invalid",
        message="Skipping invalid persisted workspace row",
        payload=payload,
    )
