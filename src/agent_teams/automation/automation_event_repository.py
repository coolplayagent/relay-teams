# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from agent_teams.persistence.db import open_sqlite


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


class AutomationEventRepository:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
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
        self._conn.commit()

    def create_event(
        self,
        record: AutomationExecutionEventRecord,
    ) -> AutomationExecutionEventRecord:
        self._conn.execute(
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
        )
        self._conn.commit()
        return record


__all__ = ["AutomationEventRepository", "AutomationExecutionEventRecord"]
