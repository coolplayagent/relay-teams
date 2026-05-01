# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import UTC, datetime
from json import dumps, loads
import sqlite3
from pathlib import Path

from pydantic import JsonValue

from relay_teams.audit.models import (
    AuditEventCreate,
    AuditEventFilter,
    AuditEventPage,
    AuditEventRecord,
    AuditEventType,
)
from relay_teams.persistence import async_fetchall
from relay_teams.persistence.sqlite_repository import SharedSqliteRepository


class AuditEventRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_audit_events (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_event_id     TEXT NOT NULL UNIQUE,
                    event_type         TEXT NOT NULL,
                    trace_id           TEXT NOT NULL,
                    run_id             TEXT NOT NULL,
                    session_id         TEXT NOT NULL,
                    task_id            TEXT,
                    instance_id        TEXT,
                    role_id            TEXT,
                    tool_call_id       TEXT,
                    span_id            TEXT,
                    parent_span_id     TEXT,
                    action             TEXT NOT NULL,
                    target             TEXT NOT NULL,
                    content_digest     TEXT,
                    content_size_bytes INTEGER,
                    command            TEXT,
                    decision_reason    TEXT,
                    outcome            TEXT NOT NULL,
                    metadata_json      TEXT NOT NULL,
                    occurred_at        TEXT NOT NULL,
                    created_at         TEXT NOT NULL
                )
                """
            )
            self._create_indexes()

        self._run_write(operation_name="init_tables", operation=operation)

    async def _init_tables_async(self) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_audit_events (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    audit_event_id     TEXT NOT NULL UNIQUE,
                    event_type         TEXT NOT NULL,
                    trace_id           TEXT NOT NULL,
                    run_id             TEXT NOT NULL,
                    session_id         TEXT NOT NULL,
                    task_id            TEXT,
                    instance_id        TEXT,
                    role_id            TEXT,
                    tool_call_id       TEXT,
                    span_id            TEXT,
                    parent_span_id     TEXT,
                    action             TEXT NOT NULL,
                    target             TEXT NOT NULL,
                    content_digest     TEXT,
                    content_size_bytes INTEGER,
                    command            TEXT,
                    decision_reason    TEXT,
                    outcome            TEXT NOT NULL,
                    metadata_json      TEXT NOT NULL,
                    occurred_at        TEXT NOT NULL,
                    created_at         TEXT NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_type_id "
                "ON security_audit_events(event_type, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_trace_id "
                "ON security_audit_events(trace_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_run_id "
                "ON security_audit_events(run_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_session_id "
                "ON security_audit_events(session_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_task_id "
                "ON security_audit_events(task_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_role_id "
                "ON security_audit_events(role_id, id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_security_audit_events_time_id "
                "ON security_audit_events(occurred_at, id)"
            )

        await self._run_async_write(
            operation_name="init_tables_async",
            operation=lambda _conn: operation(),
        )

    def _create_indexes(self) -> None:
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_type_id "
            "ON security_audit_events(event_type, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_trace_id "
            "ON security_audit_events(trace_id, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_run_id "
            "ON security_audit_events(run_id, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_session_id "
            "ON security_audit_events(session_id, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_task_id "
            "ON security_audit_events(task_id, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_role_id "
            "ON security_audit_events(role_id, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_security_audit_events_time_id "
            "ON security_audit_events(occurred_at, id)"
        )

    def append(self, event: AuditEventCreate) -> AuditEventRecord:
        created_at = datetime.now(UTC)
        self._run_write(
            operation_name="append",
            operation=lambda: self._conn.execute(
                """
                INSERT INTO security_audit_events(
                    audit_event_id, event_type, trace_id, run_id, session_id,
                    task_id, instance_id, role_id, tool_call_id, span_id,
                    parent_span_id, action, target, content_digest,
                    content_size_bytes, command, decision_reason, outcome,
                    metadata_json, occurred_at, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _event_insert_values(event=event, created_at=created_at),
            ),
        )
        return self.get_by_audit_event_id(event.audit_event_id)

    async def append_async(self, event: AuditEventCreate) -> AuditEventRecord:
        created_at = datetime.now(UTC)

        async def operation() -> None:
            conn = await self._get_async_conn()
            cursor = await conn.execute(
                """
                INSERT INTO security_audit_events(
                    audit_event_id, event_type, trace_id, run_id, session_id,
                    task_id, instance_id, role_id, tool_call_id, span_id,
                    parent_span_id, action, target, content_digest,
                    content_size_bytes, command, decision_reason, outcome,
                    metadata_json, occurred_at, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _event_insert_values(event=event, created_at=created_at),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="append_async",
            operation=lambda _conn: operation(),
        )
        return await self.get_by_audit_event_id_async(event.audit_event_id)

    def get_by_audit_event_id(self, audit_event_id: str) -> AuditEventRecord:
        with self._lock:
            row = self._conn.execute(
                _SELECT_COLUMNS + " FROM security_audit_events WHERE audit_event_id=?",
                (audit_event_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown audit_event_id: {audit_event_id}")
        return _row_to_record(row)

    async def get_by_audit_event_id_async(
        self, audit_event_id: str
    ) -> AuditEventRecord:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                _SELECT_COLUMNS + " FROM security_audit_events WHERE audit_event_id=?",
                (audit_event_id,),
            )
        )
        if not rows:
            raise KeyError(f"Unknown audit_event_id: {audit_event_id}")
        return _row_to_record(rows[0])

    def list_events(self, query: AuditEventFilter) -> AuditEventPage:
        sql, parameters = _list_query_parts(query)
        with self._lock:
            rows = self._conn.execute(sql, parameters).fetchall()
        return _page_from_rows(rows, limit=query.limit)

    async def list_events_async(self, query: AuditEventFilter) -> AuditEventPage:
        sql, parameters = _list_query_parts(query)
        rows = await self._run_async_read(
            lambda conn: async_fetchall(conn, sql, parameters)
        )
        return _page_from_rows(rows, limit=query.limit)


_SELECT_COLUMNS = (
    "SELECT id, audit_event_id, event_type, trace_id, run_id, session_id, "
    "task_id, instance_id, role_id, tool_call_id, span_id, parent_span_id, "
    "action, target, content_digest, content_size_bytes, command, "
    "decision_reason, outcome, metadata_json, occurred_at, created_at"
)


def _event_insert_values(
    *,
    event: AuditEventCreate,
    created_at: datetime,
) -> tuple[object, ...]:
    return (
        event.audit_event_id,
        event.event_type.value,
        event.trace_id,
        event.run_id,
        event.session_id,
        event.task_id,
        event.instance_id,
        event.role_id,
        event.tool_call_id,
        event.span_id,
        event.parent_span_id,
        event.action,
        event.target,
        event.content_digest,
        event.content_size_bytes,
        event.command,
        event.decision_reason,
        event.outcome,
        dumps(event.metadata, ensure_ascii=False, sort_keys=True),
        event.occurred_at.isoformat(),
        created_at.isoformat(),
    )


def _list_query_parts(query: AuditEventFilter) -> tuple[str, tuple[object, ...]]:
    clauses = ["id > ?"]
    parameters: list[object] = [query.after_id]
    if query.event_type is not None:
        clauses.append("event_type = ?")
        parameters.append(query.event_type.value)
    if query.trace_id is not None:
        clauses.append("trace_id = ?")
        parameters.append(query.trace_id)
    if query.run_id is not None:
        clauses.append("run_id = ?")
        parameters.append(query.run_id)
    if query.session_id is not None:
        clauses.append("session_id = ?")
        parameters.append(query.session_id)
    if query.task_id is not None:
        clauses.append("task_id = ?")
        parameters.append(query.task_id)
    if query.role_id is not None:
        clauses.append("role_id = ?")
        parameters.append(query.role_id)
    if query.since is not None:
        clauses.append("occurred_at >= ?")
        parameters.append(query.since.isoformat())
    if query.until is not None:
        clauses.append("occurred_at <= ?")
        parameters.append(query.until.isoformat())
    sql = (
        _SELECT_COLUMNS
        + " FROM security_audit_events WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id ASC LIMIT ?"
    )
    parameters.append(query.limit + 1)
    return sql, tuple(parameters)


def _page_from_rows(
    rows: list[sqlite3.Row],
    *,
    limit: int,
) -> AuditEventPage:
    has_more = len(rows) > limit
    visible_rows = rows[:limit]
    items = tuple(_row_to_record(row) for row in visible_rows)
    next_after_id = items[-1].id if has_more and items else None
    return AuditEventPage(items=items, next_after_id=next_after_id)


def _row_to_record(row: sqlite3.Row) -> AuditEventRecord:
    return AuditEventRecord(
        id=int(row["id"]),
        audit_event_id=str(row["audit_event_id"]),
        event_type=AuditEventType(str(row["event_type"])),
        trace_id=str(row["trace_id"]),
        run_id=str(row["run_id"]),
        session_id=str(row["session_id"]),
        task_id=_optional_text(row["task_id"]),
        instance_id=_optional_text(row["instance_id"]),
        role_id=_optional_text(row["role_id"]),
        tool_call_id=_optional_text(row["tool_call_id"]),
        span_id=_optional_text(row["span_id"]),
        parent_span_id=_optional_text(row["parent_span_id"]),
        action=str(row["action"]),
        target=str(row["target"]),
        content_digest=_optional_text(row["content_digest"]),
        content_size_bytes=_optional_int(row["content_size_bytes"]),
        command=_optional_text(row["command"]),
        decision_reason=_optional_text(row["decision_reason"]),
        outcome=str(row["outcome"]),
        metadata=_json_object(str(row["metadata_json"])),
        occurred_at=datetime.fromisoformat(str(row["occurred_at"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValueError(f"Invalid persisted integer value: {value}")


def _json_object(value: str) -> dict[str, JsonValue]:
    loaded: object = loads(value)
    if not isinstance(loaded, dict):
        return {}
    result: dict[str, JsonValue] = {}
    for key, item in loaded.items():
        result[str(key)] = _json_value(item)
    return result


def _json_value(value: object) -> JsonValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)
