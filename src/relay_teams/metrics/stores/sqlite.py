# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from relay_teams.metrics.models import MetricEvent, MetricScope
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)


class MetricPointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MetricScope
    scope_id: str
    metric_name: str = Field(min_length=1)
    bucket_start: datetime
    tags_json: str
    value: float
    recorded_at: datetime


class SqliteMetricAggregateStore(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_points (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope        TEXT NOT NULL,
                    scope_id     TEXT NOT NULL,
                    metric_name  TEXT NOT NULL,
                    bucket_start TEXT NOT NULL,
                    tags_json    TEXT NOT NULL,
                    value        REAL NOT NULL,
                    recorded_at  TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metric_points_scope
                ON metric_points(scope, scope_id, bucket_start)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_metric_points_metric
                ON metric_points(metric_name, bucket_start)
                """
            )

        self._run_write(operation_name="init_tables", operation=operation)

    def record(self, event: MetricEvent) -> None:
        bucket_start = event.occurred_at.astimezone(timezone.utc).replace(
            second=0,
            microsecond=0,
        )
        tags_json = json.dumps(dict(event.tags.normalized_items()), sort_keys=True)
        recorded_at = event.occurred_at.astimezone(timezone.utc).isoformat()
        scopes = (
            (MetricScope.GLOBAL, "global"),
            (MetricScope.SESSION, event.tags.session_id),
            (MetricScope.RUN, event.tags.run_id),
        )

        def operation() -> None:
            for scope, scope_id in scopes:
                if not scope_id:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO metric_points(
                        scope, scope_id, metric_name, bucket_start,
                        tags_json, value, recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope.value,
                        scope_id,
                        event.definition_name,
                        bucket_start.isoformat(),
                        tags_json,
                        event.value,
                        recorded_at,
                    ),
                )

        self._run_write(operation_name="record", operation=operation)

    async def record_async(self, event: MetricEvent) -> None:
        bucket_start = event.occurred_at.astimezone(timezone.utc).replace(
            second=0,
            microsecond=0,
        )
        tags_json = json.dumps(dict(event.tags.normalized_items()), sort_keys=True)
        recorded_at = event.occurred_at.astimezone(timezone.utc).isoformat()
        scopes = (
            (MetricScope.GLOBAL, "global"),
            (MetricScope.SESSION, event.tags.session_id),
            (MetricScope.RUN, event.tags.run_id),
        )

        async def operation() -> None:
            conn = await self._get_async_conn()
            for scope, scope_id in scopes:
                if not scope_id:
                    continue
                await conn.execute(
                    """
                    INSERT INTO metric_points(
                        scope, scope_id, metric_name, bucket_start,
                        tags_json, value, recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        scope.value,
                        scope_id,
                        event.definition_name,
                        bucket_start.isoformat(),
                        tags_json,
                        event.value,
                        recorded_at,
                    ),
                )

        await self._run_async_write(
            operation_name="record", operation=lambda _: operation()
        )

    def query_points(
        self,
        *,
        scope: MetricScope,
        scope_id: str,
        time_window_minutes: int,
    ) -> tuple[MetricPointRecord, ...]:
        threshold = datetime.now(tz=timezone.utc) - timedelta(
            minutes=time_window_minutes
        )
        rows = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT scope, scope_id, metric_name, bucket_start,
                       tags_json, value, recorded_at
                FROM metric_points
                WHERE scope=? AND scope_id=? AND bucket_start>=?
                ORDER BY bucket_start ASC, id ASC
                """,
                (
                    scope.value,
                    scope_id,
                    threshold.replace(second=0, microsecond=0).isoformat(),
                ),
            ).fetchall()
        )
        return tuple(self._row_to_record(row) for row in rows)

    async def query_points_async(
        self, *, scope: MetricScope, scope_id: str, time_window_minutes: int
    ) -> tuple[MetricPointRecord, ...]:
        threshold = datetime.now(tz=timezone.utc) - timedelta(
            minutes=time_window_minutes
        )

        async def operation() -> list[sqlite3.Row]:
            conn = await self._get_async_conn()
            return await async_fetchall(
                conn,
                """
                SELECT scope, scope_id, metric_name, bucket_start,
                       tags_json, value, recorded_at
                FROM metric_points
                WHERE scope=? AND scope_id=? AND bucket_start>=?
                ORDER BY bucket_start ASC, id ASC
                """,
                (
                    scope.value,
                    scope_id,
                    threshold.replace(second=0, microsecond=0).isoformat(),
                ),
            )

        rows = await self._run_async_read(lambda _: operation())
        return tuple(self._row_to_record(row) for row in rows)

    def latest_recorded_at(
        self,
        *,
        scope: MetricScope,
        scope_id: str,
    ) -> str | None:
        row = self._run_read(
            lambda: self._conn.execute(
                """
                SELECT MAX(recorded_at) AS recorded_at
                FROM metric_points
                WHERE scope=? AND scope_id=?
                """,
                (scope.value, scope_id),
            ).fetchone()
        )
        if row is None:
            return None
        value = row["recorded_at"]
        if value is None:
            return None
        return str(value)

    async def latest_recorded_at_async(
        self, *, scope: MetricScope, scope_id: str
    ) -> str | None:
        async def operation() -> sqlite3.Row | None:
            conn = await self._get_async_conn()
            return await async_fetchone(
                conn,
                """
                SELECT MAX(recorded_at) AS recorded_at
                FROM metric_points
                WHERE scope=? AND scope_id=?
                """,
                (scope.value, scope_id),
            )

        row = await self._run_async_read(lambda _: operation())
        if row is None:
            return None
        value = row["recorded_at"]
        if value is None:
            return None
        return str(value)

    def delete_by_session(self, session_id: str) -> None:
        def operation() -> None:
            self._conn.execute(
                "DELETE FROM metric_points WHERE scope=? AND scope_id=?",
                (MetricScope.SESSION.value, session_id),
            )
            self._conn.execute(
                """
                DELETE FROM metric_points
                WHERE scope=? AND tags_json LIKE ?
                """,
                (MetricScope.GLOBAL.value, f'%"session_id": "{session_id}"%'),
            )
            self._conn.execute(
                """
                DELETE FROM metric_points
                WHERE scope=? AND tags_json LIKE ?
                """,
                (MetricScope.RUN.value, f'%"session_id": "{session_id}"%'),
            )

        self._run_write(operation_name="delete_by_session", operation=operation)

    async def delete_by_session_async(self, session_id: str) -> None:
        async def operation() -> None:
            conn = await self._get_async_conn()
            await conn.execute(
                "DELETE FROM metric_points WHERE scope=? AND scope_id=?",
                (MetricScope.SESSION.value, session_id),
            )
            await conn.execute(
                """
                DELETE FROM metric_points
                WHERE scope=? AND tags_json LIKE ?
                """,
                (MetricScope.GLOBAL.value, f'%"session_id": "{session_id}"%'),
            )
            await conn.execute(
                """
                DELETE FROM metric_points
                WHERE scope=? AND tags_json LIKE ?
                """,
                (MetricScope.RUN.value, f'%"session_id": "{session_id}"%'),
            )

        await self._run_async_write(
            operation_name="delete_by_session",
            operation=lambda _: operation(),
        )

    def _row_to_record(self, row: sqlite3.Row) -> MetricPointRecord:
        return MetricPointRecord(
            scope=MetricScope(str(row["scope"])),
            scope_id=str(row["scope_id"]),
            metric_name=str(row["metric_name"]),
            bucket_start=datetime.fromisoformat(str(row["bucket_start"])),
            tags_json=str(row["tags_json"]),
            value=float(row["value"]),
            recorded_at=datetime.fromisoformat(str(row["recorded_at"])),
        )
