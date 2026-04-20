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
    WorkspaceMountCapabilities,
    WorkspaceMountProvider,
    WorkspaceMountRecord,
    WorkspaceLocalMountConfig,
    WorkspaceRecord,
    WorkspaceSshMountConfig,
    WorkspaceProfile,
    default_mount_capabilities,
    default_workspace_profile,
    legacy_workspace_mount_from_profile,
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
                    default_mount_name TEXT NOT NULL DEFAULT 'default',
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
            if "default_mount_name" not in columns:
                self._conn.execute(
                    "ALTER TABLE workspaces ADD COLUMN default_mount_name TEXT NOT NULL DEFAULT 'default'"
                )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_mounts (
                    workspace_id TEXT NOT NULL,
                    mount_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_config_json TEXT NOT NULL,
                    working_directory TEXT NOT NULL,
                    readable_paths_json TEXT NOT NULL,
                    writable_paths_json TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    branch_name TEXT,
                    source_root_path TEXT,
                    forked_from_workspace_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, mount_name)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_workspace_mounts_workspace
                ON workspace_mounts(workspace_id)
                """
            )
            self._migrate_legacy_workspace_rows()

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
        mounts: tuple[WorkspaceMountRecord, ...] | None = None,
        default_mount_name: str = "default",
        root_path: Path | None = None,
        profile: WorkspaceProfile | None = None,
    ) -> WorkspaceRecord:
        resolved_mounts = mounts
        if resolved_mounts is None:
            if root_path is None:
                raise ValueError(
                    "Workspace repository create requires root_path or mounts"
                )
            resolved_mounts = (
                legacy_workspace_mount_from_profile(
                    root_path=root_path.resolve(),
                    profile=profile or default_workspace_profile(),
                    mount_name=default_mount_name,
                ),
            )
        now = datetime.now(tz=timezone.utc).isoformat()
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            default_mount_name=default_mount_name,
            mounts=resolved_mounts,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )

        def operation() -> None:
            self._conn.execute(
                """
                INSERT INTO workspaces(
                    workspace_id,
                    root_path,
                    backend,
                    profile_json,
                    default_mount_name,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.workspace_id,
                    record.default_mount.root_reference,
                    "filesystem",
                    "{}",
                    record.default_mount_name,
                    now,
                    now,
                ),
            )
            for mount in record.mounts:
                self._insert_mount_row(
                    workspace_id=record.workspace_id,
                    mount=mount,
                    created_at=now,
                    updated_at=now,
                )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
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
            mount_rows = self._conn.execute(
                """
                SELECT * FROM workspace_mounts
                WHERE workspace_id=?
                ORDER BY mount_name ASC
                """,
                (workspace_id,),
            ).fetchall()
        if row is None:
            raise KeyError(f"Unknown workspace_id: {workspace_id}")
        try:
            return self._to_record(row=row, mount_rows=mount_rows)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_workspace_row(row=row, error=exc)
            raise KeyError(f"Unknown workspace_id: {workspace_id}") from exc

    def update(
        self,
        *,
        workspace_id: str,
        mounts: tuple[WorkspaceMountRecord, ...],
        default_mount_name: str,
    ) -> WorkspaceRecord:
        existing = self.get(workspace_id)
        now = datetime.now(tz=timezone.utc).isoformat()
        record = WorkspaceRecord(
            workspace_id=workspace_id,
            default_mount_name=default_mount_name,
            mounts=mounts,
            created_at=existing.created_at,
            updated_at=datetime.fromisoformat(now),
        )

        def operation() -> None:
            updated = self._conn.execute(
                """
                UPDATE workspaces
                SET root_path=?,
                    backend=?,
                    profile_json=?,
                    default_mount_name=?,
                    updated_at=?
                WHERE workspace_id=?
                """,
                (
                    record.default_mount.root_reference,
                    "filesystem",
                    "{}",
                    record.default_mount_name,
                    now,
                    workspace_id,
                ),
            )
            if updated.rowcount == 0:
                raise KeyError(f"Unknown workspace_id: {workspace_id}")
            self._conn.execute(
                "DELETE FROM workspace_mounts WHERE workspace_id=?",
                (workspace_id,),
            )
            for mount in record.mounts:
                self._insert_mount_row(
                    workspace_id=record.workspace_id,
                    mount=mount,
                    created_at=existing.created_at.isoformat(),
                    updated_at=now,
                )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="WorkspaceRepository",
            operation_name="update",
        )
        return record

    def list_all(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM workspaces ORDER BY created_at DESC"
            ).fetchall()
            mount_rows = self._conn.execute(
                """
                SELECT * FROM workspace_mounts
                ORDER BY workspace_id ASC, mount_name ASC
                """
            ).fetchall()
        mounts_by_workspace: dict[str, list[sqlite3.Row]] = {}
        for row in mount_rows:
            mounts_by_workspace.setdefault(str(row["workspace_id"]), []).append(row)
        records: list[WorkspaceRecord] = []
        for row in rows:
            try:
                records.append(
                    self._to_record(
                        row=row,
                        mount_rows=tuple(
                            mounts_by_workspace.get(str(row["workspace_id"]), [])
                        ),
                    )
                )
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                _log_invalid_workspace_row(row=row, error=exc)
        return tuple(records)

    def delete(self, workspace_id: str) -> None:
        def operation() -> None:
            self._conn.execute(
                "DELETE FROM workspace_mounts WHERE workspace_id=?",
                (workspace_id,),
            )
            self._conn.execute(
                "DELETE FROM workspaces WHERE workspace_id=?",
                (workspace_id,),
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
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

    def _insert_mount_row(
        self,
        *,
        workspace_id: str,
        mount: WorkspaceMountRecord,
        created_at: str,
        updated_at: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO workspace_mounts(
                workspace_id,
                mount_name,
                provider,
                provider_config_json,
                working_directory,
                readable_paths_json,
                writable_paths_json,
                capabilities_json,
                branch_name,
                source_root_path,
                forked_from_workspace_id,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                mount.mount_name,
                mount.provider.value,
                json.dumps(
                    mount.provider_config.model_dump(mode="json"), ensure_ascii=False
                ),
                mount.working_directory,
                json.dumps(list(mount.readable_paths), ensure_ascii=False),
                json.dumps(list(mount.writable_paths), ensure_ascii=False),
                json.dumps(
                    (
                        mount.capabilities or default_mount_capabilities(mount.provider)
                    ).model_dump(mode="json"),
                    ensure_ascii=False,
                ),
                mount.branch_name,
                mount.source_root_path,
                mount.forked_from_workspace_id,
                created_at,
                updated_at,
            ),
        )

    def _migrate_legacy_workspace_rows(self) -> None:
        rows = self._conn.execute("SELECT * FROM workspaces").fetchall()
        for row in rows:
            workspace_id = str(row["workspace_id"])
            mount_row = self._conn.execute(
                """
                SELECT 1 FROM workspace_mounts
                WHERE workspace_id=?
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
            if mount_row is not None:
                continue
            mount_name = (
                str(row["default_mount_name"] or "default").strip() or "default"
            )
            profile_raw = str(row["profile_json"] or "{}")
            loaded = json.loads(profile_raw)
            profile = (
                WorkspaceProfile.model_validate(loaded)
                if isinstance(loaded, dict) and loaded
                else default_workspace_profile()
            )
            mount = legacy_workspace_mount_from_profile(
                root_path=Path(str(row["root_path"])).resolve(),
                profile=profile,
                mount_name=mount_name,
            )
            created_at = (
                str(row["created_at"])
                if str(row["created_at"]).strip()
                else datetime.now(tz=timezone.utc).isoformat()
            )
            updated_at = (
                str(row["updated_at"]) if str(row["updated_at"]).strip() else created_at
            )
            self._insert_mount_row(
                workspace_id=workspace_id,
                mount=mount,
                created_at=created_at,
                updated_at=updated_at,
            )
            self._conn.execute(
                """
                UPDATE workspaces
                SET default_mount_name=?
                WHERE workspace_id=?
                """,
                (mount_name, workspace_id),
            )

    def _to_record(
        self,
        *,
        row: sqlite3.Row,
        mount_rows: tuple[sqlite3.Row, ...] | list[sqlite3.Row],
    ) -> WorkspaceRecord:
        workspace_id = require_persisted_identifier(
            row["workspace_id"],
            field_name="workspace_id",
        )
        mounts = tuple(self._to_mount_record(row=item) for item in mount_rows)
        return WorkspaceRecord(
            workspace_id=workspace_id,
            default_mount_name=require_persisted_identifier(
                row["default_mount_name"],
                field_name="default_mount_name",
            ),
            mounts=mounts,
            created_at=_require_workspace_timestamp(
                row=row,
                workspace_id=workspace_id,
                field_name="created_at",
            ),
            updated_at=_require_workspace_timestamp(
                row=row,
                workspace_id=workspace_id,
                field_name="updated_at",
            ),
        )

    def _to_mount_record(self, *, row: sqlite3.Row) -> WorkspaceMountRecord:
        provider = WorkspaceMountProvider(str(row["provider"]))
        provider_config_loaded = json.loads(str(row["provider_config_json"] or "{}"))
        if provider == WorkspaceMountProvider.LOCAL:
            provider_config = WorkspaceLocalMountConfig.model_validate(
                provider_config_loaded
            )
        else:
            provider_config = WorkspaceSshMountConfig.model_validate(
                provider_config_loaded
            )
        readable_paths = tuple(
            str(item)
            for item in json.loads(str(row["readable_paths_json"] or "[]"))
            if str(item).strip()
        )
        writable_paths = tuple(
            str(item)
            for item in json.loads(str(row["writable_paths_json"] or "[]"))
            if str(item).strip()
        )
        capabilities_loaded = json.loads(str(row["capabilities_json"] or "{}"))
        capabilities = (
            WorkspaceMountCapabilities.model_validate(capabilities_loaded)
            if isinstance(capabilities_loaded, dict) and capabilities_loaded
            else default_mount_capabilities(provider)
        )
        return WorkspaceMountRecord(
            mount_name=require_persisted_identifier(
                row["mount_name"],
                field_name="mount_name",
            ),
            provider=provider,
            provider_config=provider_config,
            working_directory=str(row["working_directory"] or "."),
            readable_paths=readable_paths or (".",),
            writable_paths=writable_paths or (".",),
            capabilities=capabilities,
            branch_name=_normalize_optional_text(row["branch_name"]),
            source_root_path=_normalize_optional_text(row["source_root_path"]),
            forked_from_workspace_id=_normalize_optional_identifier(
                row["forked_from_workspace_id"]
            ),
        )


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_optional_identifier(value: object) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return None
    return require_persisted_identifier(
        normalized,
        field_name="forked_from_workspace_id",
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
