# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, ValidationError

from relay_teams.automation.automation_models import (
    AutomationDeliveryEvent,
    AutomationFeishuBinding,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationRunConfig,
    AutomationScheduleMode,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class AutomationProjectNameConflictError(ValueError):
    pass


class AutomationProjectRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
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
                    delivery_binding_json TEXT,
                    delivery_events_json TEXT NOT NULL DEFAULT '[]',
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
            self._ensure_column(
                "automation_projects",
                "delivery_binding_json",
                "TEXT",
            )
            self._ensure_column(
                "automation_projects",
                "delivery_events_json",
                "TEXT NOT NULL DEFAULT '[]'",
            )

        self._run_write(operation_name="init_tables", operation=operation)

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        columns = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(str(row["name"]) == column for row in columns):
            return
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def create(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        try:
            self._run_write(
                operation_name="create",
                operation=lambda: self._conn.execute(
                    """
                    INSERT INTO automation_projects(
                        automation_project_id, name, display_name, status, workspace_id, prompt,
                        schedule_mode, cron_expression, run_at, timezone,
                        run_config_json, delivery_binding_json, delivery_events_json,
                        trigger_id, last_session_id, last_run_started_at,
                        last_error, next_run_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._to_row(record),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "automation_projects.name" in str(exc).lower():
                raise AutomationProjectNameConflictError(
                    f"Automation project name already exists: {record.name}"
                ) from exc
            raise
        return record

    def update(self, record: AutomationProjectRecord) -> AutomationProjectRecord:
        try:
            self._run_write(
                operation_name="update",
                operation=lambda: self._conn.execute(
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
                        delivery_binding_json=?,
                        delivery_events_json=?,
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
                        _binding_to_json(record.delivery_binding),
                        _events_to_json(record.delivery_events),
                        record.trigger_id,
                        record.last_session_id,
                        _to_iso(record.last_run_started_at),
                        record.last_error,
                        _to_iso(record.next_run_at),
                        record.updated_at.isoformat(),
                        record.automation_project_id,
                    ),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if "automation_projects.name" in str(exc).lower():
                raise AutomationProjectNameConflictError(
                    f"Automation project name already exists: {record.name}"
                ) from exc
            raise
        return record

    def get(self, automation_project_id: str) -> AutomationProjectRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM automation_projects
                WHERE automation_project_id=?
                """,
                (automation_project_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown automation_project_id: {automation_project_id}")
        try:
            return self._to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_automation_row(row=row, error=exc)
            raise KeyError(
                f"Unknown automation_project_id: {automation_project_id}"
            ) from exc

    def list_all(self) -> tuple[AutomationProjectRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM automation_projects
                ORDER BY created_at DESC
                """
            ).fetchall()
        )
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def list_due(self, now: datetime) -> tuple[AutomationProjectRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
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
        )
        return tuple(
            record for row in rows if (record := self._record_or_none(row)) is not None
        )

    def delete(self, automation_project_id: str) -> None:
        self._run_write(
            operation_name="delete",
            operation=lambda: self._conn.execute(
                """
                DELETE FROM automation_projects
                WHERE automation_project_id=?
                """,
                (automation_project_id,),
            ),
        )

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
            _binding_to_json(record.delivery_binding),
            _events_to_json(record.delivery_events),
            record.trigger_id,
            record.last_session_id,
            _to_iso(record.last_run_started_at),
            record.last_error,
            _to_iso(record.next_run_at),
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
        )

    def _to_record(self, row: sqlite3.Row) -> AutomationProjectRecord:
        automation_project_id = require_persisted_identifier(
            row["automation_project_id"],
            field_name="automation_project_id",
        )
        created_at, updated_at = _load_required_project_timestamps(
            row=row,
            automation_project_id=automation_project_id,
        )
        return AutomationProjectRecord(
            automation_project_id=automation_project_id,
            name=str(row["name"]),
            display_name=str(row["display_name"]),
            status=AutomationProjectStatus(str(row["status"])),
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            prompt=str(row["prompt"]),
            schedule_mode=AutomationScheduleMode(str(row["schedule_mode"])),
            cron_expression=normalize_persisted_text(row["cron_expression"]),
            run_at=_optional_project_timestamp(
                row=row,
                automation_project_id=automation_project_id,
                field_name="run_at",
            ),
            timezone=str(row["timezone"]),
            run_config=_run_config_from_json(row["run_config_json"]),
            delivery_binding=_binding_from_json(row["delivery_binding_json"]),
            delivery_events=_events_from_json(row["delivery_events_json"]),
            trigger_id=require_persisted_identifier(
                row["trigger_id"],
                field_name="trigger_id",
            ),
            last_session_id=normalize_persisted_text(row["last_session_id"]),
            last_run_started_at=_optional_project_timestamp(
                row=row,
                automation_project_id=automation_project_id,
                field_name="last_run_started_at",
            ),
            last_error=(
                str(row["last_error"]) if row["last_error"] is not None else None
            ),
            next_run_at=_optional_project_timestamp(
                row=row,
                automation_project_id=automation_project_id,
                field_name="next_run_at",
            ),
            created_at=created_at,
            updated_at=updated_at,
        )

    def _record_or_none(self, row: sqlite3.Row) -> AutomationProjectRecord | None:
        try:
            return self._to_record(row)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            _log_invalid_automation_row(row=row, error=exc)
            return None


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _binding_to_json(binding: AutomationFeishuBinding | None) -> str | None:
    if binding is None:
        return None
    return json.dumps(binding.model_dump(mode="json"))


def _binding_from_json(value: object) -> AutomationFeishuBinding | None:
    if value is None:
        return None
    payload = str(value).strip()
    if not payload:
        return None
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError("Invalid automation delivery binding payload")
    normalized = dict(parsed)
    normalized["session_id"] = normalize_persisted_text(parsed.get("session_id"))
    return AutomationFeishuBinding.model_validate(normalized)


def _events_to_json(events: tuple[AutomationDeliveryEvent, ...]) -> str:
    return json.dumps([event.value for event in events])


def _events_from_json(value: object) -> tuple[AutomationDeliveryEvent, ...]:
    payload = str(value or "").strip() or "[]"
    parsed = json.loads(payload)
    if not isinstance(parsed, list):
        return ()
    return tuple(AutomationDeliveryEvent(str(item)) for item in parsed)


def _run_config_from_json(value: object) -> AutomationRunConfig:
    payload = json.loads(str(value))
    if not isinstance(payload, dict):
        raise ValueError("Invalid automation run config payload")
    normalized = dict(payload)
    normalized["orchestration_preset_id"] = normalize_persisted_text(
        payload.get("orchestration_preset_id")
    )
    return AutomationRunConfig.model_validate(normalized)


def _load_required_project_timestamps(
    *,
    row: sqlite3.Row,
    automation_project_id: str,
) -> tuple[datetime, datetime]:
    created_at = parse_persisted_datetime_or_none(row["created_at"])
    updated_at = parse_persisted_datetime_or_none(row["updated_at"])
    if created_at is None:
        _log_invalid_automation_timestamp(
            automation_project_id=automation_project_id,
            field_name="created_at",
            raw_preview=_persisted_value_preview(row["created_at"]),
        )
        raise ValueError("Invalid persisted created_at")
    if updated_at is None:
        _log_invalid_automation_timestamp(
            automation_project_id=automation_project_id,
            field_name="updated_at",
            raw_preview=_persisted_value_preview(row["updated_at"]),
        )
        raise ValueError("Invalid persisted updated_at")
    return created_at, updated_at


def _optional_project_timestamp(
    *,
    row: sqlite3.Row,
    automation_project_id: str,
    field_name: str,
) -> datetime | None:
    raw_value = row[field_name]
    if normalize_persisted_text(raw_value) is None:
        return None
    parsed = parse_persisted_datetime_or_none(raw_value)
    if parsed is not None:
        return parsed
    _log_invalid_automation_timestamp(
        automation_project_id=automation_project_id,
        field_name=field_name,
        raw_preview=_persisted_value_preview(raw_value),
    )
    return None


def _persisted_value_preview(value: object) -> str:
    if value is None:
        return "<null>"
    return str(value)[:200]


def _log_invalid_automation_timestamp(
    *,
    automation_project_id: str,
    field_name: str,
    raw_preview: str,
) -> None:
    payload: dict[str, JsonValue] = {
        "automation_project_id": automation_project_id,
        "field_name": field_name,
        "raw_preview": raw_preview,
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="automation.repository.timestamp_invalid",
        message="Invalid persisted automation project timestamp",
        payload=payload,
    )


def _log_invalid_automation_row(*, row: sqlite3.Row, error: Exception) -> None:
    payload: dict[str, JsonValue] = {
        "automation_project_id": _persisted_value_preview(row["automation_project_id"]),
        "workspace_id": _persisted_value_preview(row["workspace_id"]),
        "trigger_id": _persisted_value_preview(row["trigger_id"]),
        "created_at": _persisted_value_preview(row["created_at"]),
        "updated_at": _persisted_value_preview(row["updated_at"]),
        "error_type": type(error).__name__,
        "error": str(error),
    }
    log_event(
        LOGGER,
        logging.WARNING,
        event="automation.repository.row_invalid",
        message="Skipping invalid persisted automation project row",
        payload=payload,
    )


__all__ = [
    "AutomationProjectNameConflictError",
    "AutomationProjectRepository",
]
