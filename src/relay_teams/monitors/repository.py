# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from relay_teams.monitors.models import (
    MonitorActionType,
    MonitorSourceKind,
    MonitorSubscriptionRecord,
    MonitorSubscriptionStatus,
    MonitorTriggerRecord,
)
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository


class MonitorRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_subscriptions (
                    monitor_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    created_by_instance_id TEXT,
                    created_by_role_id TEXT,
                    tool_call_id TEXT,
                    status TEXT NOT NULL,
                    rule_json TEXT NOT NULL,
                    action_json TEXT NOT NULL,
                    trigger_count INTEGER NOT NULL DEFAULT 0,
                    last_triggered_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    stopped_at TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_monitor_subscriptions_run
                ON monitor_subscriptions(run_id, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_monitor_subscriptions_source
                ON monitor_subscriptions(source_kind, source_key, status, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS monitor_triggers (
                    monitor_trigger_id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    event_name TEXT NOT NULL,
                    dedupe_key TEXT,
                    body_text TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_monitor_triggers_monitor
                ON monitor_triggers(monitor_id, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_monitor_triggers_dedupe
                ON monitor_triggers(monitor_id, dedupe_key)
                """
            )

        self._run_write(operation_name="init_tables", operation=operation)

    def create_subscription(
        self,
        record: MonitorSubscriptionRecord,
    ) -> MonitorSubscriptionRecord:
        self._run_write(
            operation_name="create_subscription",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO monitor_subscriptions(
                    monitor_id,
                    run_id,
                    session_id,
                    source_kind,
                    source_key,
                    created_by_instance_id,
                    created_by_role_id,
                    tool_call_id,
                    status,
                    rule_json,
                    action_json,
                    trigger_count,
                    last_triggered_at,
                    last_error,
                    created_at,
                    updated_at,
                    stopped_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.monitor_id,
                    record.run_id,
                    record.session_id,
                    record.source_kind.value,
                    record.source_key,
                    record.created_by_instance_id,
                    record.created_by_role_id,
                    record.tool_call_id,
                    record.status.value,
                    record.rule.model_dump_json(),
                    record.action.model_dump_json(),
                    record.trigger_count,
                    _isoformat(record.last_triggered_at),
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    _isoformat(record.stopped_at),
                ),
            ),
        )
        return record

    def update_subscription(
        self,
        record: MonitorSubscriptionRecord,
    ) -> MonitorSubscriptionRecord:
        self._run_write(
            operation_name="update_subscription",
            operation=lambda: self._conn.execute(
                """
                UPDATE monitor_subscriptions
                SET
                    run_id=?,
                    session_id=?,
                    source_kind=?,
                    source_key=?,
                    created_by_instance_id=?,
                    created_by_role_id=?,
                    tool_call_id=?,
                    status=?,
                    rule_json=?,
                    action_json=?,
                    trigger_count=?,
                    last_triggered_at=?,
                    last_error=?,
                    created_at=?,
                    updated_at=?,
                    stopped_at=?
                WHERE monitor_id=?
                """,
                (
                    record.run_id,
                    record.session_id,
                    record.source_kind.value,
                    record.source_key,
                    record.created_by_instance_id,
                    record.created_by_role_id,
                    record.tool_call_id,
                    record.status.value,
                    record.rule.model_dump_json(),
                    record.action.model_dump_json(),
                    record.trigger_count,
                    _isoformat(record.last_triggered_at),
                    record.last_error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    _isoformat(record.stopped_at),
                    record.monitor_id,
                ),
            ),
        )
        return record

    def get_subscription(self, monitor_id: str) -> MonitorSubscriptionRecord:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM monitor_subscriptions
                WHERE monitor_id=?
                """,
                (monitor_id,),
            ).fetchone()
        )
        if row is None:
            raise KeyError(f"Unknown monitor: {monitor_id}")
        return _subscription_from_row(row)

    def list_for_run(self, run_id: str) -> tuple[MonitorSubscriptionRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM monitor_subscriptions
                WHERE run_id=?
                ORDER BY created_at DESC
                """,
                (run_id,),
            ).fetchall()
        )
        return tuple(_subscription_from_row(row) for row in rows)

    def list_active_for_source(
        self,
        *,
        source_kind: str,
        source_key: str,
    ) -> tuple[MonitorSubscriptionRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM monitor_subscriptions
                WHERE source_kind=? AND source_key=? AND status=?
                ORDER BY created_at DESC
                """,
                (
                    source_kind,
                    source_key,
                    MonitorSubscriptionStatus.ACTIVE.value,
                ),
            ).fetchall()
        )
        return tuple(_subscription_from_row(row) for row in rows)

    def create_trigger(self, record: MonitorTriggerRecord) -> MonitorTriggerRecord:
        self._run_write(
            operation_name="create_trigger",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO monitor_triggers(
                    monitor_trigger_id,
                    monitor_id,
                    run_id,
                    session_id,
                    source_kind,
                    source_key,
                    event_name,
                    dedupe_key,
                    body_text,
                    attributes_json,
                    raw_payload_json,
                    action_type,
                    occurred_at,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.monitor_trigger_id,
                    record.monitor_id,
                    record.run_id,
                    record.session_id,
                    record.source_kind.value,
                    record.source_key,
                    record.event_name,
                    record.dedupe_key,
                    record.body_text,
                    json.dumps(record.attributes, ensure_ascii=False, sort_keys=True),
                    record.raw_payload_json,
                    record.action_type.value,
                    record.occurred_at.isoformat(),
                    record.created_at.isoformat(),
                ),
            ),
        )
        return record

    def has_trigger_dedupe_key(self, *, monitor_id: str, dedupe_key: str) -> bool:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT 1
                FROM monitor_triggers
                WHERE monitor_id=? AND dedupe_key=?
                LIMIT 1
                """,
                (monitor_id, dedupe_key),
            ).fetchone()
        )
        return row is not None

    def list_triggers_for_monitor(
        self, monitor_id: str
    ) -> tuple[MonitorTriggerRecord, ...]:
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT * FROM monitor_triggers
                WHERE monitor_id=?
                ORDER BY created_at DESC
                """,
                (monitor_id,),
            ).fetchall()
        )
        return tuple(_trigger_from_row(row) for row in rows)


def _subscription_from_row(row: sqlite3.Row) -> MonitorSubscriptionRecord:
    return MonitorSubscriptionRecord(
        monitor_id=str(row["monitor_id"]),
        run_id=str(row["run_id"]),
        session_id=str(row["session_id"]),
        source_kind=MonitorSourceKind(str(row["source_kind"])),
        source_key=str(row["source_key"]),
        created_by_instance_id=_nullable_text(row["created_by_instance_id"]),
        created_by_role_id=_nullable_text(row["created_by_role_id"]),
        tool_call_id=_nullable_text(row["tool_call_id"]),
        status=MonitorSubscriptionStatus(str(row["status"])),
        rule=json.loads(str(row["rule_json"])),
        action=json.loads(str(row["action_json"])),
        trigger_count=int(row["trigger_count"]),
        last_triggered_at=_nullable_datetime(row["last_triggered_at"]),
        last_error=_nullable_text(row["last_error"]),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        stopped_at=_nullable_datetime(row["stopped_at"]),
    )


def _trigger_from_row(row: sqlite3.Row) -> MonitorTriggerRecord:
    return MonitorTriggerRecord(
        monitor_trigger_id=str(row["monitor_trigger_id"]),
        monitor_id=str(row["monitor_id"]),
        run_id=str(row["run_id"]),
        session_id=str(row["session_id"]),
        source_kind=MonitorSourceKind(str(row["source_kind"])),
        source_key=str(row["source_key"]),
        event_name=str(row["event_name"]),
        dedupe_key=_nullable_text(row["dedupe_key"]),
        body_text=str(row["body_text"]),
        attributes=json.loads(str(row["attributes_json"])),
        raw_payload_json=str(row["raw_payload_json"]),
        action_type=MonitorActionType(str(row["action_type"])),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _nullable_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _nullable_datetime(value: object) -> datetime | None:
    normalized = _nullable_text(value)
    if normalized is None:
        return None
    return datetime.fromisoformat(normalized)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
