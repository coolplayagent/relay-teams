# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from agent_teams.automation.automation_models import (
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from agent_teams.persistence.db import open_sqlite


class AutomationProjectNameConflictError(ValueError):
    pass


class AutomationProjectRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS automation_projects (
                automation_project_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                status TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT 'automation-system',
                prompt TEXT NOT NULL,
                schedule_mode TEXT NOT NULL,
                cron_expression TEXT,
                run_at TEXT,
                timezone TEXT NOT NULL,
                run_config_json TEXT NOT NULL,
                trigger_id TEXT NOT NULL UNIQUE,
                last_session_id TEXT,
                last_run_started_at TEXT,
                last_error TEXT,
                next_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_automation_projects_schedule
            ON automation_projects(status, next_run_at)
            """
        )
        self._ensure_column(
            "automation_projects",
            "workspace_id",
            "TEXT NOT NULL DEFAULT 'automation-system'",
        )
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in columns):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def create(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        try:
            self._conn.execute(
                """
                INSERT INTO automation_projects(
                    automation_project_id, name, display_name, status, workspace_id, prompt,
                    schedule_mode, cron_expression, run_at, timezone,
                    run_config_json, trigger_id, last_session_id, last_run_started_at,
                    last_error, next_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._to_row(record),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            if "automation_projects.name" in str(exc).lower():
                raise AutomationProjectNameConflictError(
                    f"Automation project name already exists: {record.name}"
                ) from exc
            raise
        return record

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        try:
            self._conn.execute(
                """
                UPDATE automation_projects
                SET name=?,
                    display_name=?,
                    status=?,
                    workspace_id=?,
                    prompt=?,
                    schedule_mode=?,
                    cron_expression=?,
                    run_at=?,
                    timezone=?,
                    run_config_json=?,
                    trigger_id=?,
                    last_session_id=?,
                    last_run_started_at=?,
                    last_error=?,
                    next_run_at=?,
                    updated_at=?
                WHERE automation_project_id=?
                """,
                (
                    record.name,
                    record.display_name,
                    record.status.value,
                    record.workspace_id,
                    record.prompt,
                    record.schedule_mode.value,
                    record.cron_expression,
                    _to_iso(record.run_at),
                    record.timezone,
                    json.dumps(record.run_config.model_dump(mode="json")),
                    record.trigger_id,
                    record.last_session_id,
                    _to_iso(record.last_run_started_at),
                    record.last_error,
                    _to_iso(record.next_run_at),
                    record.updated_at.isoformat(),
                    record.automation_project_id,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            if "automation_projects.name" in str(exc).lower():
                raise AutomationProjectNameConflictError(
                    f"Automation project name already exists: {record.name}"
                ) from exc
            raise
        return record

    def get(self, automation_project_id: str) -> AutomationProjectRecord:
        row = self._conn.execute(
            """
            SELECT * FROM automation_projects
            WHERE automation_project_id=?
            """,
            (automation_project_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown automation_project_id: {automation_project_id}")
        return self._to_record(row)

    def list_all(self) -> tuple[AutomationProjectRecord, ...]:
        rows = self._conn.execute(
            """
            SELECT * FROM automation_projects
            ORDER BY created_at DESC
            """
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def list_due(self, now: datetime) -> tuple[AutomationProjectRecord, ...]:
        rows = self._conn.execute(
            """
            SELECT * FROM automation_projects
            WHERE status=? AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (
                AutomationProjectStatus.ENABLED.value,
                now.isoformat(),
            ),
        ).fetchall()
        return tuple(self._to_record(row) for row in rows)

    def delete(self, automation_project_id: str) -> None:
        self._conn.execute(
            """
            DELETE FROM automation_projects
            WHERE automation_project_id=?
            """,
            (automation_project_id,),
        )
        self._conn.commit()

    def _to_row(self, record: AutomationProjectRecord) -> tuple[object, ...]:
        return (
            record.automation_project_id,
            record.name,
            record.display_name,
            record.status.value,
            record.workspace_id,
            record.prompt,
            record.schedule_mode.value,
            record.cron_expression,
            _to_iso(record.run_at),
            record.timezone,
            json.dumps(record.run_config.model_dump(mode="json")),
            record.trigger_id,
            record.last_session_id,
            _to_iso(record.last_run_started_at),
            record.last_error,
            _to_iso(record.next_run_at),
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        )

    def _to_record(self, row: sqlite3.Row) -> AutomationProjectRecord:
        return AutomationProjectRecord(
            automation_project_id=str(row["automation_project_id"]),
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            status=AutomationProjectStatus(str(row["status"])),
            workspace_id=str(row["workspace_id"]),
            prompt=str(row["prompt"]),
            schedule_mode=AutomationScheduleMode(str(row["schedule_mode"])),
            cron_expression=(
                str(row["cron_expression"])
                if row["cron_expression"] is not None
                else None
            ),
            run_at=_from_iso(row["run_at"]),
            timezone=str(row["timezone"]),
            run_config=AutomationRunConfig.model_validate(
                json.loads(str(row["run_config_json"]))
            ),
            trigger_id=str(row["trigger_id"]),
            last_session_id=(
                str(row["last_session_id"])
                if row["last_session_id"] is not None
                else None
            ),
            last_run_started_at=_from_iso(row["last_run_started_at"]),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            next_run_at=_from_iso(row["next_run_at"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
        )


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value: object) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


__all__ = [
    "AutomationProjectNameConflictError",
    "AutomationProjectRepository",
]
