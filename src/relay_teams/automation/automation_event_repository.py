# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from relay_teams.persistence.sqlite_repository import SharedSqliteRepository


class AutomationExecutionEventRecord:
    def __init__(
        self,
        *,
        event_id: str,
        automation_project_id: str,
        reason: str,
        payload: dict[str, JsonValue],
        metadata: dict[str, str],
        occurred_at: datetime,
        created_at: datetime,
    ) -> None:
        self.event_id = event_id
        self.automation_project_id = automation_project_id
        self.reason = reason
        self.payload = payload
        self.metadata = metadata
        self.occurred_at = occurred_at
        self.created_at = created_at


class AutomationEventRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS automation_execution_events (
                    event_id TEXT PRIMARY KEY,
                    automation_project_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_automation_execution_events_project
                ON automation_execution_events(automation_project_id, created_at DESC)
                """
            )

        self._run_write(
            operation_name="init_tables",
            operation=operation,
        )

    def create_event(
        self,
        record: AutomationExecutionEventRecord,
    ) -> AutomationExecutionEventRecord:
        self._run_write(
            operation_name="create_event",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO automation_execution_events(
                    event_id,
                    automation_project_id,
                    reason,
                    payload_json,
                    metadata_json,
                    occurred_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.event_id,
                    record.automation_project_id,
                    record.reason,
                    json.dumps(record.payload),
                    json.dumps(record.metadata),
                    record.occurred_at.isoformat(),
                    record.created_at.isoformat(),
                ),
            ),
        )
        return record


__all__ = ["AutomationEventRepository", "AutomationExecutionEventRecord"]
