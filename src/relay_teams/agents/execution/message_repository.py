# -*- coding: utf-8 -*-
from __future__ import annotations

from pydantic import JsonValue

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelResponse,
    ToolCallPart,
)

from relay_teams.persistence.db import open_sqlite, run_sqlite_write_with_retry
from relay_teams.agents.execution.tool_args_repair import repair_tool_args
from relay_teams.agents.execution.tool_call_history import (
    collect_safe_row_ids,
    normalize_replayed_messages_to_safe_boundary,
)
from relay_teams.agents.tasks.task_status_sanitizer import sanitize_task_status_payload
from relay_teams.sessions.session_history_marker_models import SessionHistoryMarkerType
from relay_teams.sessions.session_history_marker_repository import (
    SessionHistoryMarkerRepository,
)


class MessageRepository:
    """Persists conversation-safe LLM message history."""

    def __init__(
        self,
        db_path: Path,
        *,
        session_history_marker_repo: SessionHistoryMarkerRepository | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._session_history_marker_repo = session_history_marker_repo
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL DEFAULT '',
                    workspace_id    TEXT NOT NULL DEFAULT '',
                    conversation_id TEXT NOT NULL DEFAULT '',
                    agent_role_id   TEXT NOT NULL DEFAULT '',
                    instance_id     TEXT NOT NULL,
                    task_id         TEXT NOT NULL,
                    trace_id        TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    message_json    TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    hidden_from_context INTEGER NOT NULL DEFAULT 0,
                    hidden_reason   TEXT NOT NULL DEFAULT '',
                    hidden_at       TEXT NOT NULL DEFAULT '',
                    hidden_marker_id TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = [
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(messages)").fetchall()
            ]
            if "session_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT ''"
                )
            if "workspace_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN workspace_id TEXT NOT NULL DEFAULT ''"
                )
            if "conversation_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN conversation_id TEXT NOT NULL DEFAULT ''"
                )
            if "agent_role_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN agent_role_id TEXT NOT NULL DEFAULT ''"
                )
            if "hidden_from_context" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN hidden_from_context INTEGER NOT NULL DEFAULT 0"
                )
            if "hidden_reason" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN hidden_reason TEXT NOT NULL DEFAULT ''"
                )
            if "hidden_at" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN hidden_at TEXT NOT NULL DEFAULT ''"
                )
            if "hidden_marker_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN hidden_marker_id TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_instance ON messages(instance_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation_visibility ON messages(conversation_id, hidden_from_context, created_at)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id)"
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="init_tables",
        )

    def append(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: Sequence[ModelMessage],
        workspace_id: str,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> None:
        if not messages:
            return
        resolved_conversation_id = conversation_id or instance_id
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._append_rows(
                session_id=session_id,
                workspace_id=workspace_id,
                resolved_conversation_id=resolved_conversation_id,
                agent_role_id=agent_role_id or "",
                instance_id=instance_id,
                task_id=task_id,
                trace_id=trace_id,
                messages=messages,
            ),
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="append",
        )

    def get_history(self, instance_id: str) -> list[ModelMessage]:
        return self._read_history(
            "SELECT session_id, message_json, created_at, hidden_from_context FROM messages WHERE instance_id=? ORDER BY id ASC",
            (instance_id,),
        )

    def get_history_for_conversation(self, conversation_id: str) -> list[ModelMessage]:
        return self._read_history(
            "SELECT session_id, message_json, created_at, hidden_from_context FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        )

    def get_messages_by_session(
        self,
        session_id: str,
        *,
        include_cleared: bool = False,
        include_hidden_from_context: bool = False,
    ) -> list[dict[str, JsonValue]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at, hidden_from_context, hidden_reason, hidden_at, hidden_marker_id "
                "FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        rows = self._filter_rows_for_read(
            rows,
            include_cleared=include_cleared,
            include_hidden_from_context=include_hidden_from_context,
        )
        if not include_hidden_from_context:
            rows = _truncate_message_rows_to_safe_boundary(rows)

        results: list[dict[str, JsonValue]] = []
        for row in rows:
            msg_list = _load_message_list(str(row["message_json"]))
            msg = msg_list[0] if msg_list and isinstance(msg_list[0], dict) else {}
            results.append(
                {
                    "conversation_id": str(row["conversation_id"] or ""),
                    "agent_role_id": str(row["agent_role_id"] or ""),
                    "instance_id": str(row["instance_id"]),
                    "task_id": str(row["task_id"]),
                    "trace_id": str(row["trace_id"]),
                    "role": str(row["role"]),
                    "created_at": str(row["created_at"]),
                    "hidden_from_context": bool(int(row["hidden_from_context"] or 0)),
                    "hidden_reason": str(row["hidden_reason"] or ""),
                    "hidden_at": str(row["hidden_at"] or ""),
                    "hidden_marker_id": str(row["hidden_marker_id"] or ""),
                    "message": msg,
                }
            )
        return _dedupe_duplicate_objective_messages(results)

    def get_messages_for_instance(
        self,
        session_id: str,
        instance_id: str,
        *,
        include_cleared: bool = False,
        include_hidden_from_context: bool = False,
    ) -> list[dict[str, JsonValue]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at, hidden_from_context, hidden_reason, hidden_at, hidden_marker_id "
                "FROM messages WHERE session_id=? AND instance_id=? ORDER BY id ASC",
                (session_id, instance_id),
            ).fetchall()
        rows = self._filter_rows_for_read(
            rows,
            include_cleared=include_cleared,
            include_hidden_from_context=include_hidden_from_context,
        )
        if not include_hidden_from_context:
            rows = _truncate_message_rows_to_safe_boundary(rows)

        results: list[dict[str, JsonValue]] = []
        for row in rows:
            msg_list = _load_message_list(str(row["message_json"]))
            msg = msg_list[0] if msg_list and isinstance(msg_list[0], dict) else {}
            results.append(
                {
                    "conversation_id": str(row["conversation_id"] or ""),
                    "agent_role_id": str(row["agent_role_id"] or ""),
                    "instance_id": str(row["instance_id"]),
                    "task_id": str(row["task_id"]),
                    "trace_id": str(row["trace_id"]),
                    "role": str(row["role"]),
                    "created_at": str(row["created_at"]),
                    "hidden_from_context": bool(int(row["hidden_from_context"] or 0)),
                    "hidden_reason": str(row["hidden_reason"] or ""),
                    "hidden_at": str(row["hidden_at"] or ""),
                    "hidden_marker_id": str(row["hidden_marker_id"] or ""),
                    "message": msg,
                }
            )
        return _dedupe_duplicate_objective_messages(results)

    def delete_by_session(self, session_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM messages WHERE session_id=?", (session_id,)
            ),
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="delete_by_session",
        )

    def delete_by_instance(self, instance_id: str) -> None:
        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=lambda: self._conn.execute(
                "DELETE FROM messages WHERE instance_id=?",
                (instance_id,),
            ),
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="delete_by_instance",
        )

    def prune_history_to_safe_boundary(self, instance_id: str) -> None:
        self._prune_to_safe_boundary(
            "SELECT id, session_id, message_json, created_at, hidden_from_context FROM messages WHERE instance_id=? ORDER BY id ASC",
            (instance_id,),
        )

    def prune_conversation_history_to_safe_boundary(self, conversation_id: str) -> None:
        self._prune_to_safe_boundary(
            "SELECT id, session_id, message_json, created_at, hidden_from_context FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        )

    def compact_conversation_history(
        self,
        conversation_id: str,
        *,
        keep_message_count: int,
        hidden_reason: str = "compaction",
        hidden_marker_id: str = "",
    ) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        safe_keep_count = max(1, int(keep_message_count))

        def operation() -> None:
            rows = self._conn.execute(
                "SELECT id, session_id, created_at, hidden_from_context FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            active_rows = self._filter_rows_for_read(
                rows,
                include_cleared=False,
                include_hidden_from_context=False,
            )
            if len(active_rows) <= safe_keep_count:
                return
            stale_ids = [
                int(row["id"])
                for row in active_rows[:-safe_keep_count]
                if isinstance(row["id"], int)
            ]
            if not stale_ids:
                return
            placeholders = ",".join("?" for _ in stale_ids)
            self._conn.execute(
                f"UPDATE messages SET hidden_from_context=1, hidden_reason=?, hidden_at=?, hidden_marker_id=? WHERE id IN ({placeholders})",
                [hidden_reason, now, hidden_marker_id, *stale_ids],
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="compact_conversation_history",
        )

    def replace_pending_user_prompt(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
        workspace_id: str,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> bool:
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        target = str(content or "").strip()
        if not target:
            return False
        resolved_conversation_id = conversation_id or instance_id
        message_json = _sanitize_message_json(
            ModelMessagesTypeAdapter.dump_json(
                [ModelRequest(parts=[UserPromptPart(content=target)])]
            ).decode()
        )

        def operation() -> bool:
            rows = self._conn.execute(
                "SELECT id, session_id, role, message_json, created_at, hidden_from_context FROM messages WHERE conversation_id=? AND task_id=? ORDER BY id ASC",
                (resolved_conversation_id, task_id),
            ).fetchall()
            active_rows = self._filter_rows_for_read(
                rows,
                include_cleared=False,
                include_hidden_from_context=False,
            )
            if _task_history_has_response(active_rows):
                return False
            replacement_ids = [
                int(row["id"])
                for row in active_rows
                if isinstance(row["id"], int)
                and str(row["role"] or "") == "user"
                and _row_is_user_prompt_only(row)
            ]
            if replacement_ids:
                primary_id = replacement_ids[0]
                self._conn.execute(
                    "UPDATE messages SET message_json=? WHERE id=?",
                    (message_json, primary_id),
                )
                stale_ids = replacement_ids[1:]
                if stale_ids:
                    placeholders = ",".join("?" for _ in stale_ids)
                    self._conn.execute(
                        f"DELETE FROM messages WHERE id IN ({placeholders})",
                        stale_ids,
                    )
                return True
            history = self.get_history_for_conversation_task(
                resolved_conversation_id,
                task_id,
            )
            if _history_ends_with_user_prompt(history, target):
                return False
            return False

        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="replace_pending_user_prompt",
        )

    def append_user_prompt_if_missing(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
        workspace_id: str,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> bool:
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        target = str(content or "").strip()
        if not target:
            return False
        resolved_conversation_id = conversation_id or instance_id
        message_json = _sanitize_message_json(
            ModelMessagesTypeAdapter.dump_json(
                [ModelRequest(parts=[UserPromptPart(content=target)])]
            ).decode()
        )

        def operation() -> bool:
            rows = self._conn.execute(
                "SELECT id, session_id, message_json, created_at, hidden_from_context FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (resolved_conversation_id,),
            ).fetchall()
            active_rows = self._filter_rows_for_read(
                rows,
                include_cleared=False,
                include_hidden_from_context=False,
            )
            allowed_ids = _safe_row_ids(active_rows)
            stale_ids = [
                int(row["id"])
                for row in active_rows
                if isinstance(row["id"], int) and int(row["id"]) not in allowed_ids
            ]
            if stale_ids:
                placeholders = ",".join("?" for _ in stale_ids)
                self._conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    stale_ids,
                )
            history = self.get_history_for_conversation_task(
                resolved_conversation_id,
                task_id,
            )
            if _history_ends_with_user_prompt(history, target):
                return False
            now = self._next_created_at(session_id=session_id)
            self._conn.execute(
                "INSERT INTO messages(session_id, workspace_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    workspace_id,
                    resolved_conversation_id,
                    agent_role_id or "",
                    instance_id,
                    task_id,
                    trace_id,
                    "user",
                    message_json,
                    now,
                ),
            )
            return True

        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="append_user_prompt_if_missing",
        )

    def append_system_prompt_if_missing(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
        workspace_id: str,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> bool:
        from pydantic_ai.messages import ModelRequest, SystemPromptPart

        target = str(content or "").strip()
        if not target:
            return False
        resolved_conversation_id = conversation_id or instance_id
        message_json = _sanitize_message_json(
            ModelMessagesTypeAdapter.dump_json(
                [ModelRequest(parts=[SystemPromptPart(content=target)])]
            ).decode()
        )

        def operation() -> bool:
            history = self.get_history_for_conversation_task(
                resolved_conversation_id,
                task_id,
            )
            if _history_ends_with_system_prompt(history, target):
                return False
            now = self._next_created_at(session_id=session_id)
            self._conn.execute(
                "INSERT INTO messages(session_id, workspace_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    workspace_id,
                    resolved_conversation_id,
                    agent_role_id or "",
                    instance_id,
                    task_id,
                    trace_id,
                    "system",
                    message_json,
                    now,
                ),
            )
            return True

        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="append_system_prompt_if_missing",
        )

    def get_history_for_task(
        self, instance_id: str, task_id: str
    ) -> list[ModelMessage]:
        return self._read_history(
            "SELECT session_id, message_json, created_at, hidden_from_context FROM messages WHERE instance_id=? AND task_id=? ORDER BY id ASC",
            (instance_id, task_id),
        )

    def get_history_for_conversation_task(
        self, conversation_id: str, task_id: str
    ) -> list[ModelMessage]:
        return self._read_history(
            "SELECT session_id, message_json, created_at, hidden_from_context FROM messages WHERE conversation_id=? AND task_id=? ORDER BY id ASC",
            (conversation_id, task_id),
        )

    def _read_history(
        self,
        query: str,
        params: tuple[str, ...],
    ) -> list[ModelMessage]:
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        rows = self._filter_rows_for_read(
            rows,
            include_cleared=False,
            include_hidden_from_context=False,
        )
        result: list[ModelMessage] = []
        for row in rows:
            msgs = ModelMessagesTypeAdapter.validate_json(
                _sanitize_message_json(str(row["message_json"]))
            )
            result.extend(msgs)
        return _truncate_model_history_to_safe_boundary(result)

    def _prune_to_safe_boundary(
        self,
        query: str,
        params: tuple[str, ...],
    ) -> None:
        def operation() -> None:
            rows = self._conn.execute(query, params).fetchall()
            active_rows = self._filter_rows_for_read(
                rows,
                include_cleared=False,
                include_hidden_from_context=False,
            )
            if not active_rows:
                return
            allowed_ids = _safe_row_ids(active_rows)
            stale_ids = [
                int(row["id"])
                for row in active_rows
                if isinstance(row["id"], int) and int(row["id"]) not in allowed_ids
            ]
            if not stale_ids:
                return
            placeholders = ",".join("?" for _ in stale_ids)
            self._conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                stale_ids,
            )

        run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="prune_to_safe_boundary",
        )

    def hide_conversation_messages_for_compaction(
        self,
        *,
        conversation_id: str,
        hide_message_count: int,
        hidden_marker_id: str,
    ) -> int:
        safe_hide_count = max(0, int(hide_message_count))
        if safe_hide_count <= 0:
            return 0
        now = datetime.now(tz=timezone.utc).isoformat()

        def operation() -> int:
            rows = self._conn.execute(
                "SELECT id, session_id, created_at, hidden_from_context FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conversation_id,),
            ).fetchall()
            active_rows = self._filter_rows_for_read(
                rows,
                include_cleared=False,
                include_hidden_from_context=False,
            )
            if not active_rows:
                return 0
            row_ids = [
                int(row["id"])
                for row in active_rows[:safe_hide_count]
                if isinstance(row["id"], int)
            ]
            if not row_ids:
                return 0
            placeholders = ",".join("?" for _ in row_ids)
            self._conn.execute(
                f"UPDATE messages SET hidden_from_context=1, hidden_reason='compaction', hidden_at=?, hidden_marker_id=? WHERE id IN ({placeholders})",
                [now, hidden_marker_id, *row_ids],
            )
            return len(row_ids)

        return run_sqlite_write_with_retry(
            conn=self._conn,
            db_path=self._db_path,
            operation=operation,
            lock=self._lock,
            repository_name="MessageRepository",
            operation_name="hide_conversation_messages_for_compaction",
        )

    def _append_rows(
        self,
        *,
        session_id: str,
        workspace_id: str,
        resolved_conversation_id: str,
        agent_role_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: Sequence[ModelMessage],
    ) -> None:
        now = self._next_created_at(session_id=session_id)
        rows = [
            (
                session_id,
                workspace_id,
                resolved_conversation_id,
                agent_role_id,
                instance_id,
                task_id,
                trace_id,
                _role(normalized_message),
                _sanitize_message_json(
                    ModelMessagesTypeAdapter.dump_json([normalized_message]).decode()
                ),
                now,
            )
            for msg in messages
            for normalized_message in [_normalize_message_for_persistence(msg)]
        ]
        self._conn.executemany(
            "INSERT INTO messages(session_id, workspace_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    def _next_created_at(self, *, session_id: str) -> str:
        candidate = datetime.now(tz=timezone.utc)
        latest_message_row = self._conn.execute(
            "SELECT created_at FROM messages WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        candidate = _ensure_after_iso_value(
            candidate,
            None
            if latest_message_row is None
            else str(latest_message_row["created_at"]),
        )
        if self._session_history_marker_repo is not None:
            latest_clear = self._session_history_marker_repo.get_latest(
                session_id,
                marker_type=SessionHistoryMarkerType.CLEAR,
            )
            if latest_clear is not None:
                candidate = _ensure_after_datetime(candidate, latest_clear.created_at)
        return candidate.isoformat()

    def _filter_rows_for_read(
        self,
        rows: Sequence[sqlite3.Row],
        *,
        include_cleared: bool,
        include_hidden_from_context: bool,
    ) -> list[sqlite3.Row]:
        filtered_rows = list(rows)
        if not include_cleared:
            filtered_rows = self._filter_rows_for_active_segments(filtered_rows)
        if include_hidden_from_context:
            return filtered_rows
        return self._filter_rows_for_visible_context(filtered_rows)

    def _filter_rows_for_active_segments(
        self,
        rows: Sequence[sqlite3.Row],
    ) -> list[sqlite3.Row]:
        if self._session_history_marker_repo is None or not rows:
            return list(rows)

        cutoff_by_session = self._latest_clear_cutoff_by_session(rows)
        if not cutoff_by_session:
            return list(rows)

        filtered: list[sqlite3.Row] = []
        for row in rows:
            session_id = str(row["session_id"] or "")
            if not session_id:
                filtered.append(row)
                continue
            cutoff = cutoff_by_session.get(session_id)
            if cutoff is None:
                filtered.append(row)
                continue
            created_at = str(row["created_at"] or "")
            if created_at > cutoff:
                filtered.append(row)
        return filtered

    def _filter_rows_for_visible_context(
        self,
        rows: Sequence[sqlite3.Row],
    ) -> list[sqlite3.Row]:
        return [row for row in rows if not bool(int(row["hidden_from_context"] or 0))]

    def _latest_clear_cutoff_by_session(
        self,
        rows: Sequence[sqlite3.Row],
    ) -> dict[str, str]:
        if self._session_history_marker_repo is None:
            return {}
        session_ids = {
            str(row["session_id"] or "") for row in rows if str(row["session_id"] or "")
        }
        if not session_ids:
            return {}
        cutoffs: dict[str, str] = {}
        for session_id in session_ids:
            latest_clear = self._session_history_marker_repo.get_latest(
                session_id,
                marker_type=SessionHistoryMarkerType.CLEAR,
            )
            if latest_clear is None:
                continue
            cutoffs[session_id] = latest_clear.created_at.isoformat()
        return cutoffs


def _role(msg: ModelMessage) -> str:
    from pydantic_ai.messages import ModelRequest, ModelResponse

    if isinstance(msg, ModelRequest):
        return "user"
    if isinstance(msg, ModelResponse):
        return "assistant"
    return "unknown"


def _sanitize_message_json(message_json: str) -> str:
    try:
        parsed = json.loads(message_json)
    except Exception:
        return message_json
    sanitized = sanitize_task_status_payload(parsed)
    return json.dumps(sanitized, ensure_ascii=False)


def _load_message_list(message_json: str) -> list[object]:
    try:
        parsed = json.loads(_sanitize_message_json(message_json))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _dedupe_duplicate_objective_messages(
    messages: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    seen_user_prompts: dict[tuple[str, str], set[str]] = {}
    deduped: list[dict[str, JsonValue]] = []
    for message in messages:
        conversation_id = str(message.get("conversation_id") or "")
        task_id = str(message.get("task_id") or "")
        repeated_user_prompt = _extract_repeatable_user_prompt(message.get("message"))
        if not repeated_user_prompt:
            deduped.append(message)
            continue
        seen_for_task = seen_user_prompts.setdefault((conversation_id, task_id), set())
        if repeated_user_prompt in seen_for_task:
            continue
        seen_for_task.add(repeated_user_prompt)
        deduped.append(message)
    return deduped


def _extract_repeatable_user_prompt(message: object) -> str | None:
    if not isinstance(message, dict):
        return None
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        return None

    prompt_chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        kind = str(part.get("part_kind") or "")
        if kind == "system-prompt":
            continue
        if kind != "user-prompt":
            return None
        content = str(part.get("content") or "")
        if not content:
            return None
        prompt_chunks.append(content)

    if not prompt_chunks:
        return None
    return "\n".join(prompt_chunks).strip() or None


def _truncate_message_rows_to_safe_boundary(
    rows: list[sqlite3.Row],
) -> list[sqlite3.Row]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(
            str(row["conversation_id"] or row["instance_id"]), []
        ).append(row)

    allowed_ids: set[int] = set()
    for conversation_rows in grouped.values():
        allowed_ids.update(_safe_row_ids(conversation_rows))
    return [
        row
        for row in rows
        if isinstance(row["id"], int) and int(row["id"]) in allowed_ids
    ]


def _truncate_model_history_to_safe_boundary(
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    return normalize_replayed_messages_to_safe_boundary(messages)


def _history_ends_with_user_prompt(
    history: Sequence[ModelMessage],
    content: str,
) -> bool:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    target = str(content or "").strip()
    if not target or not history:
        return False
    last = history[-1]
    if not isinstance(last, ModelRequest):
        return False
    prompt_parts = [part for part in last.parts if isinstance(part, UserPromptPart)]
    if len(prompt_parts) != len(last.parts):
        return False
    combined = "\n".join(
        str(part.content or "").strip() for part in prompt_parts
    ).strip()
    return combined == target


def _task_history_has_response(rows: Sequence[sqlite3.Row]) -> bool:
    from pydantic_ai.messages import ModelResponse

    for row in rows:
        messages = ModelMessagesTypeAdapter.validate_json(
            _sanitize_message_json(str(row["message_json"]))
        )
        if any(isinstance(message, ModelResponse) for message in messages):
            return True
    return False


def _row_is_user_prompt_only(row: sqlite3.Row) -> bool:
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = ModelMessagesTypeAdapter.validate_json(
        _sanitize_message_json(str(row["message_json"]))
    )
    if len(messages) != 1:
        return False
    message = messages[0]
    if not isinstance(message, ModelRequest):
        return False
    prompt_parts = [part for part in message.parts if isinstance(part, UserPromptPart)]
    return bool(prompt_parts) and len(prompt_parts) == len(message.parts)


def _history_ends_with_system_prompt(
    history: Sequence[ModelMessage],
    content: str,
) -> bool:
    from pydantic_ai.messages import ModelRequest, SystemPromptPart

    target = str(content or "").strip()
    if not target or not history:
        return False
    last = history[-1]
    if not isinstance(last, ModelRequest):
        return False
    prompt_parts = [part for part in last.parts if isinstance(part, SystemPromptPart)]
    if len(prompt_parts) != len(last.parts):
        return False
    combined = "\n".join(
        str(part.content or "").strip() for part in prompt_parts
    ).strip()
    return combined == target


def _normalize_message_for_persistence(message: ModelMessage) -> ModelMessage:
    if not isinstance(message, ModelResponse):
        return message
    next_parts = list(message.parts)
    changed = False
    for index, part in enumerate(message.parts):
        if not isinstance(part, ToolCallPart) or not isinstance(part.args, str):
            continue
        repaired = repair_tool_args(part.args)
        if not repaired.repair_applied and not repaired.fallback_invalid_json:
            continue
        next_parts[index] = replace(part, args=repaired.arguments_json)
        changed = True
    if not changed:
        return message
    return replace(message, parts=next_parts)


def _safe_row_ids(rows: Sequence[sqlite3.Row]) -> set[int]:
    history_rows: list[tuple[int, Sequence[ModelMessage]]] = []
    for row in rows:
        row_id = row["id"]
        if not isinstance(row_id, int):
            continue
        messages = ModelMessagesTypeAdapter.validate_json(
            _sanitize_message_json(str(row["message_json"]))
        )
        history_rows.append((row_id, messages))
    return collect_safe_row_ids(history_rows)


def _ensure_after_iso_value(candidate: datetime, raw_value: str | None) -> datetime:
    if raw_value is None:
        return candidate
    try:
        reference = datetime.fromisoformat(raw_value)
    except ValueError:
        return candidate
    return _ensure_after_datetime(candidate, reference)


def _ensure_after_datetime(candidate: datetime, reference: datetime) -> datetime:
    normalized_reference = (
        reference.replace(tzinfo=timezone.utc)
        if reference.tzinfo is None
        else reference.astimezone(timezone.utc)
    )
    if candidate > normalized_reference:
        return candidate
    return normalized_reference + timedelta(microseconds=1)
