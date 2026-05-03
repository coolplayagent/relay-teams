# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, ConfigDict

from relay_teams.agents.tasks.enums import TaskSpecStrictness
from relay_teams.tools.runtime.guardrails import (
    RuntimeGuardrailAction,
    RuntimeGuardrailEvaluation,
    RuntimeGuardrailLayer,
    RuntimeGuardrailRuleType,
)

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS guardrail_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    instance_id TEXT NOT NULL DEFAULT '',
    role_id TEXT NOT NULL DEFAULT '',
    tool_name TEXT NOT NULL DEFAULT '',
    layer TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    action TEXT NOT NULL,
    triggered INTEGER NOT NULL DEFAULT 0,
    original_text_excerpt TEXT NOT NULL DEFAULT '',
    modified_text_excerpt TEXT NOT NULL DEFAULT '',
    triggered_rule_names TEXT NOT NULL DEFAULT '[]',
    strictness TEXT NOT NULL DEFAULT 'medium',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    evaluated_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_run_id
    ON guardrail_audit(run_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_task_id
    ON guardrail_audit(task_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_role_id
    ON guardrail_audit(role_id);
CREATE INDEX IF NOT EXISTS idx_guardrail_audit_evaluated_at
    ON guardrail_audit(evaluated_at);
"""

_TRUNCATE_CHARS = 2000


class GuardrailAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    audit_id: str
    session_id: str
    run_id: str
    task_id: str
    instance_id: str
    role_id: str
    tool_name: str
    layer: RuntimeGuardrailLayer
    rule_type: RuntimeGuardrailRuleType
    rule_id: str
    action: RuntimeGuardrailAction
    triggered: bool
    original_text_excerpt: str
    modified_text_excerpt: str
    triggered_rule_names: tuple[str, ...]
    strictness: TaskSpecStrictness
    metadata_json: dict[str, object]
    evaluated_at: str
    created_at: str


def _truncate(value: str, limit: int = _TRUNCATE_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


class GuardrailAuditRepository:
    """Persist guardrail evaluations to SQLite for compliance and debugging."""

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

    async def record_evaluation_async(
        self,
        *,
        evaluation: RuntimeGuardrailEvaluation,
        strictness: TaskSpecStrictness,
        session_id: str = "",
        run_id: str = "",
        task_id: str = "",
        instance_id: str = "",
        role_id: str = "",
        tool_name: str = "",
    ) -> int:
        """Record a guardrail evaluation to the audit trail."""
        triggered_findings = [
            f for f in evaluation.findings if f.action != RuntimeGuardrailAction.ALLOW
        ]
        is_triggered = len(triggered_findings) > 0
        audit_id = str(uuid.uuid4())
        evaluated_at = datetime.now(tz=timezone.utc).isoformat()

        original_excerpt = ""
        modified_excerpt = ""
        triggered_rule_names: list[str] = []

        for finding in triggered_findings:
            triggered_rule_names.append(finding.rule_id)
            original_excerpt = finding.message[:_TRUNCATE_CHARS]
            if finding.details.get("command"):
                modified_excerpt = str(finding.details["command"])[:_TRUNCATE_CHARS]

        metadata: dict[str, object] = {
            "finding_count": len(evaluation.findings),
            "triggered_count": len(triggered_findings),
        }

        async with aiosqlite.connect(str(self._db_path)) as conn:
            cursor = await conn.execute(
                """\
                INSERT INTO guardrail_audit (
                    audit_id, session_id, run_id, task_id, instance_id,
                    role_id, tool_name, layer, rule_type, rule_id,
                    action, triggered, original_text_excerpt,
                    modified_text_excerpt, triggered_rule_names,
                    strictness, metadata_json, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    session_id,
                    run_id,
                    task_id,
                    instance_id,
                    role_id,
                    tool_name,
                    "",
                    "",
                    "",
                    "",
                    1 if is_triggered else 0,
                    _truncate(original_excerpt),
                    _truncate(modified_excerpt),
                    json.dumps(triggered_rule_names),
                    strictness.value,
                    json.dumps(metadata),
                    evaluated_at,
                ),
            )
            for finding in triggered_findings:
                await conn.execute(
                    """\
                    INSERT INTO guardrail_audit (
                        audit_id, session_id, run_id, task_id, instance_id,
                        role_id, tool_name, layer, rule_type, rule_id,
                        action, triggered, original_text_excerpt,
                        modified_text_excerpt, triggered_rule_names,
                        strictness, metadata_json, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_id,
                        session_id,
                        run_id,
                        task_id,
                        instance_id,
                        role_id,
                        tool_name,
                        finding.layer.value,
                        finding.rule_type.value,
                        finding.rule_id,
                        finding.action.value,
                        1 if is_triggered else 0,
                        _truncate(finding.message),
                        _truncate(str(finding.details.get("command", ""))),
                        json.dumps([finding.rule_id]),
                        strictness.value,
                        json.dumps(
                            {"details": {k: str(v) for k, v in finding.details.items()}}
                        ),
                        evaluated_at,
                    ),
                )
            await conn.commit()
            row_id = cursor.lastrowid or 0
            return row_id

    async def query_evaluations_async(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        role_id: str | None = None,
        layer: RuntimeGuardrailLayer | None = None,
        action: RuntimeGuardrailAction | None = None,
        triggered_only: bool = False,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GuardrailAuditRecord]:
        """Query guardrail audit records with optional filters."""
        conditions: list[str] = []
        params: list[object] = []

        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if task_id is not None:
            conditions.append("task_id = ?")
            params.append(task_id)
        if role_id is not None:
            conditions.append("role_id = ?")
            params.append(role_id)
        if layer is not None:
            conditions.append("layer = ?")
            params.append(layer.value)
        if action is not None:
            conditions.append("action = ?")
            params.append(action.value)
        if triggered_only:
            conditions.append("triggered = 1")
        if since is not None:
            conditions.append("evaluated_at >= ?")
            params.append(since)
        if until is not None:
            conditions.append("evaluated_at <= ?")
            params.append(until)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        params.append(limit)
        params.append(offset)

        query = (
            f"SELECT * FROM guardrail_audit {where_clause} "
            f"ORDER BY id DESC LIMIT ? OFFSET ?"
        )

        rows: list[GuardrailAuditRecord] = []
        async with aiosqlite.connect(str(self._db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = await conn.execute(query, tuple(params))
            col_names: tuple[str, ...] = ()
            if cursor.description is not None:
                col_names = tuple(desc[0] for desc in cursor.description)
            async for row in cursor:
                try:
                    record = _row_to_record(row, col_names)
                    rows.append(record)
                except (ValueError, KeyError):
                    continue
            await cursor.close()
        return rows


def _row_to_record(
    row: sqlite3.Row,
    column_names: tuple[str, ...] = (),
) -> GuardrailAuditRecord:
    if column_names:
        names = column_names
    else:
        names = (
            "id",
            "audit_id",
            "session_id",
            "run_id",
            "task_id",
            "instance_id",
            "role_id",
            "tool_name",
            "layer",
            "rule_type",
            "rule_id",
            "action",
            "triggered",
            "original_text_excerpt",
            "modified_text_excerpt",
            "triggered_rule_names",
            "strictness",
            "metadata_json",
            "evaluated_at",
            "created_at",
        )
    raw = dict(zip(names, row))

    triggered_names_raw = raw.get("triggered_rule_names", "[]")
    if isinstance(triggered_names_raw, str):
        try:
            triggered_names_list = json.loads(triggered_names_raw)
        except json.JSONDecodeError:
            triggered_names_list = []
    elif isinstance(triggered_names_raw, list):
        triggered_names_list = triggered_names_raw
    else:
        triggered_names_list = []

    metadata_raw = raw.get("metadata_json", "{}")
    if isinstance(metadata_raw, str):
        try:
            metadata_val: dict[str, object] = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata_val = {}
    elif isinstance(metadata_raw, dict):
        metadata_val = metadata_raw
    else:
        metadata_val = {}

    layer_val = raw.get("layer", "") or RuntimeGuardrailLayer.PRE_EXECUTION.value
    rule_type_val = (
        raw.get("rule_type", "") or RuntimeGuardrailRuleType.TOOL_ALLOWLIST.value
    )
    action_val = raw.get("action", "") or RuntimeGuardrailAction.WARN.value
    strictness_val = raw.get("strictness", "") or TaskSpecStrictness.MEDIUM.value

    return GuardrailAuditRecord(
        id=raw["id"],
        audit_id=raw["audit_id"],
        session_id=raw.get("session_id", ""),
        run_id=raw.get("run_id", ""),
        task_id=raw.get("task_id", ""),
        instance_id=raw.get("instance_id", ""),
        role_id=raw.get("role_id", ""),
        tool_name=raw.get("tool_name", ""),
        layer=RuntimeGuardrailLayer(layer_val),
        rule_type=RuntimeGuardrailRuleType(rule_type_val),
        rule_id=raw.get("rule_id", ""),
        action=RuntimeGuardrailAction(action_val),
        triggered=bool(raw.get("triggered", 0)),
        original_text_excerpt=raw.get("original_text_excerpt", ""),
        modified_text_excerpt=raw.get("modified_text_excerpt", ""),
        triggered_rule_names=tuple(str(n) for n in triggered_names_list),
        strictness=TaskSpecStrictness(strictness_val),
        metadata_json=metadata_val,
        evaluated_at=raw.get("evaluated_at", ""),
        created_at=raw.get("created_at", ""),
    )
