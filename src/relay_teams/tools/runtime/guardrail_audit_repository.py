# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS runtime_guardrail_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    role_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    rule_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    timestamp TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_guardrail_audit_task_id
    ON runtime_guardrail_audit(task_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_role_id
    ON runtime_guardrail_audit(role_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_timestamp
    ON runtime_guardrail_audit(timestamp);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_action
    ON runtime_guardrail_audit(action);
"""


class GuardrailAuditEntry(BaseModel):
    """A single guardrail audit finding persisted to the database."""

    model_config = ConfigDict(extra="forbid")

    id: int = 0
    task_id: str
    role_id: str = ""
    tool_name: str = ""
    rule_id: str = ""
    action: str = ""
    severity: str = ""
    message: str = ""
    detail_json: str = "{}"
    timestamp: str = ""
    session_id: str = ""
    trace_id: str = ""
    instance_id: str = ""


class GuardrailAuditRepository:
    """Persist and query runtime guardrail audit findings."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.executescript(_CREATE_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    def insert_entry(self, entry: GuardrailAuditEntry) -> int:
        """Insert a guardrail audit finding. Returns the row ID."""
        now = entry.timestamp or datetime.now(tz=timezone.utc).isoformat()
        conn = sqlite3.connect(str(self._db_path))
        try:
            cursor = conn.execute(
                "INSERT INTO runtime_guardrail_audit "
                "(task_id, role_id, tool_name, rule_id, action, severity, "
                "message, detail_json, timestamp, session_id, trace_id, instance_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.task_id,
                    entry.role_id,
                    entry.tool_name,
                    entry.rule_id,
                    entry.action,
                    entry.severity,
                    entry.message,
                    entry.detail_json,
                    now,
                    entry.session_id,
                    entry.trace_id,
                    entry.instance_id,
                ),
            )
            conn.commit()
            return cursor.lastrowid or 0
        finally:
            conn.close()

    def query_entries(
        self,
        *,
        task_id: str | None = None,
        role_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[GuardrailAuditEntry], int]:
        """Query audit entries with optional filters.

        Returns (entries, total_count).
        """
        conn = sqlite3.connect(str(self._db_path))
        try:
            conditions: list[str] = []
            params: list[object] = []

            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)
            if role_id is not None:
                conditions.append("role_id = ?")
                params.append(role_id)
            if action is not None:
                conditions.append("action = ?")
                params.append(action)

            where = " AND ".join(conditions) if conditions else "1=1"
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM runtime_guardrail_audit WHERE {where}",
                tuple(params),
            ).fetchone()
            total = count_row[0] if count_row else 0

            params_list = list(params) + [limit, offset]
            rows = conn.execute(
                f"SELECT * FROM runtime_guardrail_audit "
                f"WHERE {where} "
                f"ORDER BY id DESC LIMIT ? OFFSET ?",
                tuple(params_list),
            ).fetchall()

            col_names: list[str] = []
            if rows:
                col_names = [
                    desc[0]
                    for desc in conn.execute(
                        "SELECT * FROM runtime_guardrail_audit WHERE 1=0"
                    ).description
                ]

            entries = [_row_to_entry(dict(zip(col_names, r))) for r in rows]
            return entries, total
        finally:
            conn.close()


def _row_to_entry(raw: dict[str, object]) -> GuardrailAuditEntry:
    raw_id = raw.get("id", 0)
    return GuardrailAuditEntry(
        id=int(raw_id) if isinstance(raw_id, (int, str, float)) else 0,
        task_id=str(raw.get("task_id", "")),
        role_id=str(raw.get("role_id", "")),
        tool_name=str(raw.get("tool_name", "")),
        rule_id=str(raw.get("rule_id", "")),
        action=str(raw.get("action", "")),
        severity=str(raw.get("severity", "")),
        message=str(raw.get("message", "")),
        detail_json=str(raw.get("detail_json", "{}")),
        timestamp=str(raw.get("timestamp", "")),
        session_id=str(raw.get("session_id", "")),
        trace_id=str(raw.get("trace_id", "")),
        instance_id=str(raw.get("instance_id", "")),
    )
