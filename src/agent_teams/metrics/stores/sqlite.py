# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from pydantic import BaseModel, ConfigDict, Field

from agent_teams.metrics.models import MetricEvent, MetricScope
from agent_teams.persistence.db import open_sqlite


class MetricPointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MetricScope
    scope_id: str
    metric_name: str = Field(min_length=1)
    bucket_start: datetime
    tags_json: str
    value: float
    recorded_at: datetime


class SqliteMetricAggregateStore:
    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
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
            self._conn.commit()

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
        with self._lock:
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
            self._conn.commit()

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
        with self._lock:
            rows = self._conn.execute(
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
        return tuple(self._row_to_record(row) for row in rows)

    def latest_recorded_at(
        self,
        *,
        scope: MetricScope,
        scope_id: str,
    ) -> str | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT MAX(recorded_at) AS recorded_at
                FROM metric_points
                WHERE scope=? AND scope_id=?
                """,
                (scope.value, scope_id),
            ).fetchone()
        if row is None:
            return None
        value = row["recorded_at"]
        if value is None:
            return None
        return str(value)

    def delete_by_session(self, session_id: str) -> None:
        with self._lock:
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
            self._conn.commit()

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
