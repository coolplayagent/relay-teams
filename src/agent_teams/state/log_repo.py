from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_teams.state.db import open_sqlite


class LogRepository:
    """Append-only structured log storage."""

    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS system_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                level         TEXT NOT NULL,
                event         TEXT,
                trace_id      TEXT,
                request_id    TEXT,
                session_id    TEXT,
                run_id        TEXT,
                task_id       TEXT,
                instance_id   TEXT,
                logger        TEXT,
                message       TEXT NOT NULL,
                payload_json  TEXT,
                error_json    TEXT,
                raw_json      TEXT NOT NULL
            )
            '''
        )
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_ts ON system_logs(ts)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_level ON system_logs(level)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_event ON system_logs(event)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_trace ON system_logs(trace_id)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_run ON system_logs(run_id)')
        self._conn.execute('CREATE INDEX IF NOT EXISTS idx_system_logs_request ON system_logs(request_id)')
        self._conn.commit()

    def append_many(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        values: list[tuple[object, ...]] = []
        for row in rows:
            payload = row.get('payload')
            err = row.get('error')
            values.append(
                (
                    row.get('ts', ''),
                    row.get('level', 'INFO'),
                    row.get('event'),
                    row.get('trace_id'),
                    row.get('request_id'),
                    row.get('session_id'),
                    row.get('run_id'),
                    row.get('task_id'),
                    row.get('instance_id'),
                    row.get('logger', ''),
                    row.get('message', ''),
                    json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
                    json.dumps(err, ensure_ascii=False, default=str) if err is not None else None,
                    json.dumps(row, ensure_ascii=False, default=str),
                )
            )

        self._conn.executemany(
            '''
            INSERT INTO system_logs(
                ts, level, event, trace_id, request_id, session_id, run_id,
                task_id, instance_id, logger, message, payload_json, error_json, raw_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            values,
        )
        self._conn.commit()
