# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from agent_teams.shared_types.json_types import JsonObject
from agent_teams.state.db import open_sqlite
from agent_teams.agents.tasks.task_status_sanitizer import sanitize_task_status_payload
from agent_teams.workspace import build_workspace_id


class MessageRepository:
    """Persists conversation-safe LLM message history."""

    def __init__(self, db_path: Path) -> None:
        self._conn = open_sqlite(db_path)
        self._conn.row_factory = sqlite3.Row
        self._lock = RLock()
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
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
                    created_at      TEXT NOT NULL
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
                "CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id)"
            )
            self._conn.commit()

    def append(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        messages: Sequence[ModelMessage],
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> None:
        if not messages:
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        resolved_workspace_id = workspace_id or build_workspace_id(session_id)
        resolved_conversation_id = conversation_id or instance_id
        rows = [
            (
                session_id,
                resolved_workspace_id,
                resolved_conversation_id,
                agent_role_id or "",
                instance_id,
                task_id,
                trace_id,
                _role(msg),
                _sanitize_message_json(
                    ModelMessagesTypeAdapter.dump_json([msg]).decode()
                ),
                now,
            )
            for msg in messages
        ]
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.executemany(
                    "INSERT INTO messages(session_id, workspace_id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def get_history(self, instance_id: str) -> list[ModelMessage]:
        return self._read_history(
            "SELECT message_json FROM messages WHERE instance_id=? ORDER BY id ASC",
            (instance_id,),
        )

    def get_history_for_conversation(self, conversation_id: str) -> list[ModelMessage]:
        return self._read_history(
            "SELECT message_json FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        )

    def get_messages_by_session(self, session_id: str) -> list[JsonObject]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at "
                "FROM messages WHERE session_id=? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        rows = _truncate_message_rows_to_safe_boundary(rows)

        results: list[JsonObject] = []
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
                    "message": msg,
                }
            )
        return _dedupe_duplicate_objective_messages(results)

    def get_messages_for_instance(
        self, session_id: str, instance_id: str
    ) -> list[JsonObject]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, conversation_id, agent_role_id, instance_id, task_id, trace_id, role, message_json, created_at "
                "FROM messages WHERE session_id=? AND instance_id=? ORDER BY id ASC",
                (session_id, instance_id),
            ).fetchall()
        rows = _truncate_message_rows_to_safe_boundary(rows)

        results: list[JsonObject] = []
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
                    "message": msg,
                }
            )
        return _dedupe_duplicate_objective_messages(results)

    def delete_by_session(self, session_id: str) -> None:
        with self._lock:
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    "DELETE FROM messages WHERE session_id=?", (session_id,)
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def prune_history_to_safe_boundary(self, instance_id: str) -> None:
        self._prune_to_safe_boundary(
            "SELECT id, message_json FROM messages WHERE instance_id=? ORDER BY id ASC",
            (instance_id,),
        )

    def prune_conversation_history_to_safe_boundary(self, conversation_id: str) -> None:
        self._prune_to_safe_boundary(
            "SELECT id, message_json FROM messages WHERE conversation_id=? ORDER BY id ASC",
            (conversation_id,),
        )

    def append_user_prompt_if_missing(
        self,
        *,
        session_id: str,
        instance_id: str,
        task_id: str,
        trace_id: str,
        content: str,
        workspace_id: str | None = None,
        conversation_id: str | None = None,
        agent_role_id: str | None = None,
    ) -> bool:
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        target = str(content or "").strip()
        if not target:
            return False
        resolved_conversation_id = conversation_id or instance_id
        with self._lock:
            self.prune_conversation_history_to_safe_boundary(resolved_conversation_id)
            history = self.get_history_for_conversation_task(
                resolved_conversation_id,
                task_id,
            )
            if _history_ends_with_user_prompt(history, target):
                return False
            self.append(
                session_id=session_id,
                workspace_id=workspace_id,
                conversation_id=resolved_conversation_id,
                agent_role_id=agent_role_id,
                instance_id=instance_id,
                task_id=task_id,
                trace_id=trace_id,
                messages=[ModelRequest(parts=[UserPromptPart(content=target)])],
            )
            return True

    def get_history_for_task(
        self, instance_id: str, task_id: str
    ) -> list[ModelMessage]:
        return self._read_history(
            "SELECT message_json FROM messages WHERE instance_id=? AND task_id=? ORDER BY id ASC",
            (instance_id, task_id),
        )

    def get_history_for_conversation_task(
        self, conversation_id: str, task_id: str
    ) -> list[ModelMessage]:
        return self._read_history(
            "SELECT message_json FROM messages WHERE conversation_id=? AND task_id=? ORDER BY id ASC",
            (conversation_id, task_id),
        )

    def _read_history(
        self,
        query: str,
        params: tuple[str, ...],
    ) -> list[ModelMessage]:
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
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
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
            if not rows:
                return
            allowed_ids = _safe_row_ids(rows)
            stale_ids = [
                int(row["id"])
                for row in rows
                if isinstance(row["id"], int) and int(row["id"]) not in allowed_ids
            ]
            if not stale_ids:
                return
            placeholders = ",".join("?" for _ in stale_ids)
            self._run_write_with_retry(
                lambda: self._conn.execute(
                    f"DELETE FROM messages WHERE id IN ({placeholders})",
                    stale_ids,
                )
            )
            self._run_write_with_retry(self._conn.commit)

    def _run_write_with_retry(self, op: Callable[[], object]) -> None:
        max_retries = 8
        delay = 0.01
        for attempt in range(max_retries + 1):
            try:
                _ = op()
                return
            except sqlite3.OperationalError as exc:
                if not _is_retryable_write_error(exc) or attempt >= max_retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 0.2)


def _is_retryable_write_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "another row available" in message
    )


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
    messages: list[JsonObject],
) -> list[JsonObject]:
    seen_user_prompts: dict[tuple[str, str], set[str]] = {}
    deduped: list[JsonObject] = []
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
    last_safe_index = 0
    for idx in range(1, len(messages) + 1):
        if not _collect_pending_tool_call_ids(messages[:idx]):
            last_safe_index = idx
    return messages[:last_safe_index]


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


def _collect_pending_tool_call_ids(messages: list[ModelMessage]) -> set[str]:
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
        ToolReturnPart,
    )

    pending: set[str] = set()
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    continue
                tool_call_id = str(part.tool_call_id or "").strip()
                if tool_call_id:
                    pending.add(tool_call_id)
            continue
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            tool_call_id = str(getattr(part, "tool_call_id", "") or "").strip()
            if not tool_call_id:
                continue
            if isinstance(part, (ToolReturnPart, RetryPromptPart)):
                pending.discard(tool_call_id)
    return pending


def _safe_row_ids(rows: Sequence[sqlite3.Row]) -> set[int]:
    last_safe_index = 0
    history: list[ModelMessage] = []
    for idx, row in enumerate(rows, start=1):
        msgs = ModelMessagesTypeAdapter.validate_json(
            _sanitize_message_json(str(row["message_json"]))
        )
        history.extend(msgs)
        if not _collect_pending_tool_call_ids(history):
            last_safe_index = idx
    safe_ids: set[int] = set()
    for row in rows[:last_safe_index]:
        row_id = row["id"]
        if isinstance(row_id, int):
            safe_ids.add(row_id)
    return safe_ids
