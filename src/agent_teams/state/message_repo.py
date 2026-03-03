from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from agent_teams.state.db import open_sqlite


class MessageRepository:
    """Persists per-instance LLM message history for multi-turn context."""

    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL DEFAULT '',
                instance_id  TEXT NOT NULL,
                task_id      TEXT NOT NULL,
                trace_id     TEXT NOT NULL,
                role         TEXT NOT NULL,
                message_json TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
            '''
        )
        columns = [r['name'] for r in self._conn.execute('PRAGMA table_info(messages)').fetchall()]
        if 'session_id' not in columns:
            self._conn.execute("ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_messages_instance ON messages(instance_id)'
        )
        self._conn.execute(
            'CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id)'
        )
        self._conn.commit()

    def append(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: list[ModelMessage],
    ) -> None:
        """Insert a batch of messages from a single run_sync call."""
        now = datetime.now(tz=timezone.utc).isoformat()
        rows = [
            (
                session_id,
                instance_id,
                task_id,
                trace_id,
                _role(msg),
                ModelMessagesTypeAdapter.dump_json([msg]).decode(),
                now,
            )
            for msg in messages
        ]
        self._conn.executemany(
            'INSERT INTO messages(session_id, instance_id, task_id, trace_id, role, message_json, created_at) '
            'VALUES(?, ?, ?, ?, ?, ?, ?)',
            rows,
        )
        self._conn.commit()

    def get_history(self, instance_id: str) -> list[ModelMessage]:
        """Return all messages for an instance ordered chronologically."""
        rows = self._conn.execute(
            'SELECT message_json FROM messages WHERE instance_id=? ORDER BY id ASC',
            (instance_id,),
        ).fetchall()
        result: list[ModelMessage] = []
        for row in rows:
            msgs = ModelMessagesTypeAdapter.validate_json(str(row['message_json']))
            result.extend(msgs)
        return result

    def get_messages_by_session(self, session_id: str) -> list[dict]:
        """Return all messages for an entire session with their DB metadata."""
        rows = self._conn.execute(
            'SELECT instance_id, task_id, trace_id, role, message_json, created_at '
            'FROM messages WHERE session_id=? ORDER BY id ASC',
            (session_id,),
        ).fetchall()
        
        import json
        results = []
        for row in rows:
            # message_json is a list [ { ... } ]
            msg_list = json.loads(str(row['message_json']))
            msg = msg_list[0] if msg_list else {}
            results.append({
                "instance_id": str(row["instance_id"]),
                "task_id": str(row["task_id"]),
                "trace_id": str(row["trace_id"]),
                "role": str(row["role"]),
                "created_at": str(row["created_at"]),
                "message": msg
            })
        return results

    def get_messages_for_instance(
        self, session_id: str, instance_id: str
    ) -> list[dict]:
        """Return all messages for a single instance scoped to one session."""
        rows = self._conn.execute(
            'SELECT instance_id, task_id, trace_id, role, message_json, created_at '
            'FROM messages WHERE session_id=? AND instance_id=? ORDER BY id ASC',
            (session_id, instance_id),
        ).fetchall()

        import json
        results = []
        for row in rows:
            msg_list = json.loads(str(row['message_json']))
            msg = msg_list[0] if msg_list else {}
            results.append(
                {
                    "instance_id": str(row["instance_id"]),
                    "task_id": str(row["task_id"]),
                    "trace_id": str(row["trace_id"]),
                    "role": str(row["role"]),
                    "created_at": str(row["created_at"]),
                    "message": msg,
                }
            )
        return results

    def delete_by_session(self, session_id: str) -> None:
        self._conn.execute('DELETE FROM messages WHERE session_id=?', (session_id,))
        self._conn.commit()

    def get_history_for_task(self, instance_id: str, task_id: str) -> list[ModelMessage]:
        """Return messages scoped to a specific task."""
        rows = self._conn.execute(
            'SELECT message_json FROM messages WHERE instance_id=? AND task_id=? ORDER BY id ASC',
            (instance_id, task_id),
        ).fetchall()
        result: list[ModelMessage] = []
        for row in rows:
            msgs = ModelMessagesTypeAdapter.validate_json(str(row['message_json']))
            result.extend(msgs)
        return result


def _role(msg: ModelMessage) -> str:
    from pydantic_ai.messages import ModelRequest, ModelResponse
    if isinstance(msg, ModelRequest):
        return 'user'
    if isinstance(msg, ModelResponse):
        return 'assistant'
    return 'unknown'
