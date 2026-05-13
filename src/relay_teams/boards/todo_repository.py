# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from pydantic import ValidationError

from relay_teams.boards.todo_models import (
    BoardTodoAttempt,
    BoardTodoAttemptStatus,
    BoardTodoAttemptType,
    BoardTodoDiagnostic,
    BoardTodoExecutionPolicy,
    BoardTodoExecutionQueueTicket,
    BoardTodoHandoffPrompt,
    BoardTodoHandoffTemplate,
    BoardTodoHandoffTemplateKind,
    BoardTodoQueueKind,
    BoardTodoQueueStatus,
    BoardTodoRuntimeTargetKind,
    BoardTodoTemplateScope,
    BoardTodoItem,
    BoardTodoSource,
    BoardTodoSourceKind,
    BoardTodoSourceProvider,
    BoardTodoSourceState,
    BoardTodoSourceType,
    BoardTodoSyncStatus,
    BoardTodoStatus,
)
from relay_teams.logger import get_logger
from relay_teams.persistence import async_fetchall, async_fetchone
from relay_teams.persistence.sqlite_repository import (
    BlockingAsyncSqliteConnection,
    SharedSqliteRepository,
)
from relay_teams.sessions.runs.run_models import RunThinkingConfig
from relay_teams.sessions.session_models import SessionMode
from relay_teams.validation import (
    normalize_persisted_text,
    parse_persisted_datetime_or_none,
    require_persisted_identifier,
)

LOGGER = get_logger(__name__)


class BoardTodoRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        def operation() -> None:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_items (
                    todo_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    source_id TEXT,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source_provider TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    repository_full_name TEXT,
                    issue_number INTEGER,
                    pull_request_number INTEGER,
                    html_url TEXT,
                    session_id TEXT,
                    run_id TEXT,
                    linked_pr_number INTEGER,
                    linked_pr_url TEXT,
                    archived_at TEXT,
                    last_synced_at TEXT,
                    source_updated_at TEXT,
                    last_status_reason TEXT,
                    item_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, source_provider, source_key)
                )
                """
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_items",
                column_name="source_id",
                definition="TEXT",
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_items",
                column_name="item_revision",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_items",
                column_name="source_updated_at",
                definition="TEXT",
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_items",
                column_name="current_attempt_id",
                definition="TEXT",
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_items",
                column_name="active_attempt_id",
                definition="TEXT",
            )
            for column_name, definition in (
                ("execution_workspace_id", "TEXT"),
                ("execution_policy", "TEXT"),
                ("runtime_target_kind", "TEXT"),
                ("runtime_target_id", "TEXT"),
                ("queue_ticket_id", "TEXT"),
            ):
                _ensure_column(
                    self._conn,
                    table_name="board_todo_items",
                    column_name=column_name,
                    definition=definition,
                )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_workspace_state (
                    workspace_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL DEFAULT 0,
                    github_issue_sync_cursor TEXT,
                    repository_full_name TEXT,
                    todo_sources_bootstrapped INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_workspace_state",
                column_name="todo_sources_bootstrapped",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_sources (
                    source_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    repository_full_name TEXT,
                    system_managed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_source_state (
                    source_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    sync_cursor TEXT,
                    last_sync_started_at TEXT,
                    last_sync_finished_at TEXT,
                    last_sync_status TEXT NOT NULL DEFAULT 'idle',
                    last_diagnostics_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_handoff_prompts (
                    prompt_ref TEXT PRIMARY KEY,
                    todo_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    template_kind TEXT NOT NULL,
                    template_source TEXT NOT NULL,
                    final_prompt_snapshot TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    todo_id TEXT NOT NULL,
                    attempt_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    session_id TEXT,
                    run_id TEXT,
                    prompt_ref TEXT,
                    summary TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                )
                """
            )
            for column_name, definition in (
                ("board_workspace_id", "TEXT"),
                ("initiated_from_workspace_id", "TEXT"),
                ("source_workspace_id", "TEXT"),
                ("execution_workspace_id", "TEXT"),
                ("execution_policy", "TEXT"),
                ("runtime_target_kind", "TEXT"),
                ("runtime_target_id", "TEXT"),
                ("queue_ticket_id", "TEXT"),
                ("handoff_initiator", "TEXT NOT NULL DEFAULT 'human'"),
                ("start_policy", "TEXT NOT NULL DEFAULT 'human_required'"),
                ("yolo", "INTEGER NOT NULL DEFAULT 1"),
                ("thinking_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                _ensure_column(
                    self._conn,
                    table_name="board_todo_attempts",
                    column_name=column_name,
                    definition=definition,
                )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_execution_queue (
                    queue_ticket_id TEXT PRIMARY KEY,
                    todo_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    prompt_ref TEXT NOT NULL,
                    queue_kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    board_workspace_id TEXT NOT NULL,
                    source_workspace_id TEXT NOT NULL,
                    initiated_from_workspace_id TEXT,
                    execution_workspace_id TEXT,
                    previous_run_id TEXT,
                    execution_policy TEXT NOT NULL,
                    runtime_target_kind TEXT,
                    runtime_target_id TEXT,
                    session_mode TEXT,
                    normal_root_role_id TEXT,
                    orchestration_preset_id TEXT,
                    yolo INTEGER NOT NULL DEFAULT 1,
                    thinking_json TEXT NOT NULL DEFAULT '{}',
                    claim_token TEXT,
                    claim_expires_at TEXT,
                    claimed_by TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    diagnostics_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            _ensure_column(
                self._conn,
                table_name="board_todo_execution_queue",
                column_name="previous_run_id",
                definition="TEXT",
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_handoff_templates (
                    template_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    template_kind TEXT NOT NULL,
                    source_id TEXT,
                    template TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, scope, template_kind, source_id)
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS board_todo_diagnostics (
                    diagnostic_id TEXT PRIMARY KEY,
                    todo_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    attempt_id TEXT,
                    queue_ticket_id TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_items_workspace_status
                ON board_todo_items(workspace_id, status, updated_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_items_workspace_revision
                ON board_todo_items(workspace_id, item_revision)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_items_run
                ON board_todo_items(run_id)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_items_linked_pr
                ON board_todo_items(repository_full_name, linked_pr_number)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_items_source_id
                ON board_todo_items(workspace_id, source_id, source_key)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_sources_workspace
                ON board_todo_sources(workspace_id, kind, enabled)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_attempts_todo
                ON board_todo_attempts(todo_id, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_handoff_prompts_todo
                ON board_todo_handoff_prompts(todo_id, created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_execution_queue_pending
                ON board_todo_execution_queue(status, created_at ASC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_execution_queue_todo
                ON board_todo_execution_queue(todo_id, status)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_board_todo_handoff_templates_lookup
                ON board_todo_handoff_templates(
                    workspace_id, scope, template_kind, source_id
                )
                """
            )

        self._run_write(operation_name="init_tables", operation=operation)

    async def create_async(self, item: BoardTodoItem) -> BoardTodoItem:
        await self._insert_async(item)
        return await self.require_async(item.todo_id)

    async def create_attempt_async(
        self,
        attempt: BoardTodoAttempt,
    ) -> BoardTodoAttempt:
        async def operation(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO board_todo_attempts (
                    attempt_id,
                    todo_id,
                    attempt_type,
                    status,
                    board_workspace_id,
                    initiated_from_workspace_id,
                    source_workspace_id,
                    execution_workspace_id,
                    execution_policy,
                    runtime_target_kind,
                    runtime_target_id,
                    queue_ticket_id,
                    handoff_initiator,
                    start_policy,
                    yolo,
                    thinking_json,
                    session_id,
                    run_id,
                    prompt_ref,
                    summary,
                    error,
                    created_at,
                    started_at,
                    finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _attempt_params(attempt),
            )

        await self._run_async_write(
            operation_name="create_attempt_async",
            operation=operation,
        )
        return await self.require_attempt_async(attempt.attempt_id)

    async def update_attempt_async(
        self,
        attempt: BoardTodoAttempt,
    ) -> BoardTodoAttempt:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                UPDATE board_todo_attempts
                SET
                    todo_id=?,
                    attempt_type=?,
                    status=?,
                    board_workspace_id=?,
                    initiated_from_workspace_id=?,
                    source_workspace_id=?,
                    execution_workspace_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    queue_ticket_id=?,
                    handoff_initiator=?,
                    start_policy=?,
                    yolo=?,
                    thinking_json=?,
                    session_id=?,
                    run_id=?,
                    prompt_ref=?,
                    summary=?,
                    error=?,
                    created_at=?,
                    started_at=?,
                    finished_at=?
                WHERE attempt_id=?
                """,
                (*_attempt_params(attempt)[1:], attempt.attempt_id),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown board todo attempt: {attempt.attempt_id}")

        await self._run_async_write(
            operation_name="update_attempt_async",
            operation=operation,
        )
        return await self.require_attempt_async(attempt.attempt_id)

    async def create_handoff_prompt_async(
        self,
        prompt: BoardTodoHandoffPrompt,
    ) -> BoardTodoHandoffPrompt:
        async def operation(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO board_todo_handoff_prompts (
                    prompt_ref,
                    todo_id,
                    attempt_id,
                    template_kind,
                    template_source,
                    final_prompt_snapshot,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                _handoff_prompt_params(prompt),
            )

        await self._run_async_write(
            operation_name="create_handoff_prompt_async",
            operation=operation,
        )
        return await self.require_handoff_prompt_async(prompt.prompt_ref)

    async def get_attempt_async(self, attempt_id: str) -> BoardTodoAttempt | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_attempts
                WHERE attempt_id=?
                """,
                (attempt_id,),
            )
        )
        return _row_to_attempt_or_none(row)

    async def require_attempt_async(self, attempt_id: str) -> BoardTodoAttempt:
        attempt = await self.get_attempt_async(attempt_id)
        if attempt is None:
            raise KeyError(f"Unknown board todo attempt: {attempt_id}")
        return attempt

    async def list_attempts_for_todo_async(
        self,
        todo_id: str,
    ) -> tuple[BoardTodoAttempt, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_attempts
                WHERE todo_id=?
                ORDER BY created_at ASC
                """,
                (todo_id,),
            )
        )
        return tuple(
            attempt for row in rows if (attempt := _row_to_attempt_or_none(row))
        )

    async def get_handoff_prompt_async(
        self,
        prompt_ref: str,
    ) -> BoardTodoHandoffPrompt | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_handoff_prompts
                WHERE prompt_ref=?
                """,
                (prompt_ref,),
            )
        )
        return _row_to_handoff_prompt_or_none(row)

    async def require_handoff_prompt_async(
        self,
        prompt_ref: str,
    ) -> BoardTodoHandoffPrompt:
        prompt = await self.get_handoff_prompt_async(prompt_ref)
        if prompt is None:
            raise KeyError(f"Unknown board todo handoff prompt: {prompt_ref}")
        return prompt

    async def create_queue_ticket_async(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket:
        async def operation(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO board_todo_execution_queue (
                    queue_ticket_id,
                    todo_id,
                    attempt_id,
                    prompt_ref,
                    queue_kind,
                    status,
                    board_workspace_id,
                    source_workspace_id,
                    initiated_from_workspace_id,
                    execution_workspace_id,
                    previous_run_id,
                    execution_policy,
                    runtime_target_kind,
                    runtime_target_id,
                    session_mode,
                    normal_root_role_id,
                    orchestration_preset_id,
                    yolo,
                    thinking_json,
                    claim_token,
                    claim_expires_at,
                    claimed_by,
                    failure_count,
                    diagnostics_json,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _queue_ticket_params(ticket),
            )

        await self._run_async_write(
            operation_name="create_queue_ticket_async",
            operation=operation,
        )
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def update_queue_ticket_async(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket:
        next_ticket = ticket.model_copy(update={"updated_at": _utc_now()})

        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """
                UPDATE board_todo_execution_queue
                SET
                    todo_id=?,
                    attempt_id=?,
                    prompt_ref=?,
                    queue_kind=?,
                    status=?,
                    board_workspace_id=?,
                    source_workspace_id=?,
                    initiated_from_workspace_id=?,
                    execution_workspace_id=?,
                    previous_run_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    session_mode=?,
                    normal_root_role_id=?,
                    orchestration_preset_id=?,
                    yolo=?,
                    thinking_json=?,
                    claim_token=?,
                    claim_expires_at=?,
                    claimed_by=?,
                    failure_count=?,
                    diagnostics_json=?,
                    created_at=?,
                    updated_at=?
                WHERE queue_ticket_id=?
                """,
                (*_queue_ticket_params(next_ticket)[1:], next_ticket.queue_ticket_id),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(
                    f"Unknown board todo queue ticket: {ticket.queue_ticket_id}"
                )

        await self._run_async_write(
            operation_name="update_queue_ticket_async",
            operation=operation,
        )
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def update_claimed_queue_ticket_async(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket | None:
        if ticket.claim_token is None:
            return None
        next_ticket = ticket.model_copy(update={"updated_at": _utc_now()})

        async def operation(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """
                UPDATE board_todo_execution_queue
                SET
                    todo_id=?,
                    attempt_id=?,
                    prompt_ref=?,
                    queue_kind=?,
                    status=?,
                    board_workspace_id=?,
                    source_workspace_id=?,
                    initiated_from_workspace_id=?,
                    execution_workspace_id=?,
                    previous_run_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    session_mode=?,
                    normal_root_role_id=?,
                    orchestration_preset_id=?,
                    yolo=?,
                    thinking_json=?,
                    claim_token=?,
                    claim_expires_at=?,
                    claimed_by=?,
                    failure_count=?,
                    diagnostics_json=?,
                    created_at=?,
                    updated_at=?
                WHERE queue_ticket_id=?
                    AND status=?
                    AND claim_token=?
                """,
                (
                    *_queue_ticket_params(next_ticket)[1:],
                    next_ticket.queue_ticket_id,
                    BoardTodoQueueStatus.CLAIMED.value,
                    ticket.claim_token,
                ),
            )
            await cursor.close()
            return cursor.rowcount == 1

        updated = await self._run_async_write(
            operation_name="update_claimed_queue_ticket_async",
            operation=operation,
        )
        if not updated:
            return None
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def release_queue_ticket_claim_async(
        self,
        ticket: BoardTodoExecutionQueueTicket,
    ) -> BoardTodoExecutionQueueTicket | None:
        if ticket.claim_token is None:
            return None
        next_ticket = ticket.model_copy(
            update={
                "status": BoardTodoQueueStatus.PENDING,
                "claim_token": None,
                "claim_expires_at": None,
                "claimed_by": None,
                "updated_at": _utc_now(),
            }
        )

        async def operation(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """
                UPDATE board_todo_execution_queue
                SET
                    todo_id=?,
                    attempt_id=?,
                    prompt_ref=?,
                    queue_kind=?,
                    status=?,
                    board_workspace_id=?,
                    source_workspace_id=?,
                    initiated_from_workspace_id=?,
                    execution_workspace_id=?,
                    previous_run_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    session_mode=?,
                    normal_root_role_id=?,
                    orchestration_preset_id=?,
                    yolo=?,
                    thinking_json=?,
                    claim_token=?,
                    claim_expires_at=?,
                    claimed_by=?,
                    failure_count=?,
                    diagnostics_json=?,
                    created_at=?,
                    updated_at=?
                WHERE queue_ticket_id=?
                    AND status=?
                    AND claim_token=?
                """,
                (
                    *_queue_ticket_params(next_ticket)[1:],
                    next_ticket.queue_ticket_id,
                    BoardTodoQueueStatus.CLAIMED.value,
                    ticket.claim_token,
                ),
            )
            await cursor.close()
            return cursor.rowcount == 1

        released = await self._run_async_write(
            operation_name="release_queue_ticket_claim_async",
            operation=operation,
        )
        if not released:
            return None
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def claim_queue_ticket_async(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        claim_token: str,
        claim_expires_at: datetime,
        claimed_by: str,
        now: datetime,
    ) -> BoardTodoExecutionQueueTicket | None:
        next_ticket = ticket.model_copy(
            update={
                "status": BoardTodoQueueStatus.CLAIMED,
                "claim_token": claim_token,
                "claim_expires_at": claim_expires_at,
                "claimed_by": claimed_by,
                "updated_at": _utc_now(),
            }
        )

        async def operation(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """
                UPDATE board_todo_execution_queue
                SET
                    todo_id=?,
                    attempt_id=?,
                    prompt_ref=?,
                    queue_kind=?,
                    status=?,
                    board_workspace_id=?,
                    source_workspace_id=?,
                    initiated_from_workspace_id=?,
                    execution_workspace_id=?,
                    previous_run_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    session_mode=?,
                    normal_root_role_id=?,
                    orchestration_preset_id=?,
                    yolo=?,
                    thinking_json=?,
                    claim_token=?,
                    claim_expires_at=?,
                    claimed_by=?,
                    failure_count=?,
                    diagnostics_json=?,
                    created_at=?,
                    updated_at=?
                WHERE queue_ticket_id=?
                    AND (
                        status=?
                        OR (
                            status=?
                            AND claim_expires_at IS NOT NULL
                            AND claim_expires_at<=?
                        )
                    )
                """,
                (
                    *_queue_ticket_params(next_ticket)[1:],
                    next_ticket.queue_ticket_id,
                    BoardTodoQueueStatus.PENDING.value,
                    BoardTodoQueueStatus.CLAIMED.value,
                    _datetime_to_text(now),
                ),
            )
            await cursor.close()
            return cursor.rowcount == 1

        claimed = await self._run_async_write(
            operation_name="claim_queue_ticket_async",
            operation=operation,
        )
        if not claimed:
            return None
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def renew_queue_ticket_claim_async(
        self,
        *,
        ticket: BoardTodoExecutionQueueTicket,
        claim_expires_at: datetime,
    ) -> BoardTodoExecutionQueueTicket | None:
        if ticket.claim_token is None:
            return None
        next_ticket = ticket.model_copy(
            update={
                "claim_expires_at": claim_expires_at,
                "updated_at": _utc_now(),
            }
        )

        async def operation(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """
                UPDATE board_todo_execution_queue
                SET claim_expires_at=?, updated_at=?
                WHERE queue_ticket_id=?
                    AND status=?
                    AND claim_token=?
                """,
                (
                    _datetime_to_text(next_ticket.claim_expires_at),
                    _datetime_to_text(next_ticket.updated_at),
                    next_ticket.queue_ticket_id,
                    BoardTodoQueueStatus.CLAIMED.value,
                    ticket.claim_token,
                ),
            )
            await cursor.close()
            return cursor.rowcount == 1

        renewed = await self._run_async_write(
            operation_name="renew_queue_ticket_claim_async",
            operation=operation,
        )
        if not renewed:
            return None
        return await self.require_queue_ticket_async(ticket.queue_ticket_id)

    async def get_queue_ticket_async(
        self,
        queue_ticket_id: str,
    ) -> BoardTodoExecutionQueueTicket | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_execution_queue
                WHERE queue_ticket_id=?
                """,
                (queue_ticket_id,),
            )
        )
        return _row_to_queue_ticket_or_none(row)

    async def require_queue_ticket_async(
        self,
        queue_ticket_id: str,
    ) -> BoardTodoExecutionQueueTicket:
        ticket = await self.get_queue_ticket_async(queue_ticket_id)
        if ticket is None:
            raise KeyError(f"Unknown board todo queue ticket: {queue_ticket_id}")
        return ticket

    async def list_pending_queue_tickets_async(
        self,
        *,
        limit: int | None = 10,
    ) -> tuple[BoardTodoExecutionQueueTicket, ...]:
        now = _utc_now()
        now_text = now.astimezone(timezone.utc).isoformat()
        limit_clause = "" if limit is None else "LIMIT ?"
        params: tuple[str, str, str] | tuple[str, str, str, int]
        if limit is None:
            params = (
                BoardTodoQueueStatus.PENDING.value,
                BoardTodoQueueStatus.CLAIMED.value,
                now_text,
            )
        else:
            params = (
                BoardTodoQueueStatus.PENDING.value,
                BoardTodoQueueStatus.CLAIMED.value,
                now_text,
                limit,
            )
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                f"""
                SELECT *
                FROM board_todo_execution_queue
                WHERE status=?
                    OR (
                        status=?
                        AND claim_expires_at IS NOT NULL
                        AND claim_expires_at<=?
                    )
                ORDER BY created_at ASC
                {limit_clause}
                """,
                params,
            )
        )
        return tuple(
            ticket
            for row in rows
            if (ticket := _row_to_queue_ticket_or_none(row))
            and _queue_ticket_is_pending_or_expired(ticket, now=now)
        )

    async def upsert_handoff_template_async(
        self,
        template: BoardTodoHandoffTemplate,
    ) -> BoardTodoHandoffTemplate:
        async def operation(conn: aiosqlite.Connection) -> None:
            if template.source_id is None:
                await conn.execute(
                    """
                    DELETE FROM board_todo_handoff_templates
                    WHERE workspace_id=?
                        AND scope=?
                        AND template_kind=?
                        AND source_id IS NULL
                    """,
                    (
                        template.workspace_id,
                        template.scope.value,
                        template.template_kind.value,
                    ),
                )
            else:
                await conn.execute(
                    """
                    DELETE FROM board_todo_handoff_templates
                    WHERE workspace_id=?
                        AND scope=?
                        AND template_kind=?
                        AND source_id=?
                    """,
                    (
                        template.workspace_id,
                        template.scope.value,
                        template.template_kind.value,
                        template.source_id,
                    ),
                )
            await conn.execute(
                """
                INSERT INTO board_todo_handoff_templates (
                    template_id,
                    workspace_id,
                    scope,
                    template_kind,
                    source_id,
                    template,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _handoff_template_params(template),
            )

        await self._run_async_write(
            operation_name="upsert_handoff_template_async",
            operation=operation,
        )
        return await self.require_handoff_template_async(template.template_id)

    async def get_handoff_template_async(
        self,
        *,
        workspace_id: str,
        template_kind: BoardTodoHandoffTemplateKind,
        source_id: str | None = None,
    ) -> BoardTodoHandoffTemplate | None:
        if source_id is not None:
            row = await self._run_async_read(
                lambda conn: async_fetchone(
                    conn,
                    """
                    SELECT *
                    FROM board_todo_handoff_templates
                    WHERE workspace_id=?
                        AND scope='source'
                        AND template_kind=?
                        AND source_id=?
                    """,
                    (workspace_id, template_kind.value, source_id),
                )
            )
            source_template = _row_to_handoff_template_or_none(row)
            if source_template is not None:
                return source_template
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_handoff_templates
                WHERE workspace_id=?
                    AND scope='workspace'
                    AND template_kind=?
                    AND source_id IS NULL
                """,
                (workspace_id, template_kind.value),
            )
        )
        return _row_to_handoff_template_or_none(row)

    async def require_handoff_template_async(
        self,
        template_id: str,
    ) -> BoardTodoHandoffTemplate:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_handoff_templates
                WHERE template_id=?
                """,
                (template_id,),
            )
        )
        template = _row_to_handoff_template_or_none(row)
        if template is None:
            raise KeyError(f"Unknown board todo handoff template: {template_id}")
        return template

    async def list_handoff_templates_async(
        self,
        *,
        workspace_id: str,
    ) -> tuple[BoardTodoHandoffTemplate, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_handoff_templates
                WHERE workspace_id=?
                ORDER BY scope ASC, template_kind ASC, source_id ASC
                """,
                (workspace_id,),
            )
        )
        return tuple(
            template
            for row in rows
            if (template := _row_to_handoff_template_or_none(row))
        )

    async def delete_handoff_template_async(
        self,
        *,
        template_id: str,
    ) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM board_todo_handoff_templates WHERE template_id=?",
                (template_id,),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown board todo handoff template: {template_id}")

        await self._run_async_write(
            operation_name="delete_handoff_template_async",
            operation=operation,
        )

    async def create_diagnostic_async(
        self,
        diagnostic: BoardTodoDiagnostic,
    ) -> BoardTodoDiagnostic:
        async def operation(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO board_todo_diagnostics (
                    diagnostic_id,
                    todo_id,
                    workspace_id,
                    kind,
                    message,
                    attempt_id,
                    queue_ticket_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _diagnostic_params(diagnostic),
            )

        await self._run_async_write(
            operation_name="create_diagnostic_async",
            operation=operation,
        )
        return diagnostic

    async def create_source_async(self, source: BoardTodoSource) -> BoardTodoSource:
        async def operation(conn: aiosqlite.Connection) -> None:
            await _ensure_unique_github_source_repository_async(
                conn,
                source=source,
                excluded_source_id=None,
            )
            await conn.execute(
                """
                INSERT INTO board_todo_sources (
                    source_id,
                    workspace_id,
                    kind,
                    provider,
                    display_name,
                    enabled,
                    repository_full_name,
                    system_managed,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _source_params(source),
            )
            await _ensure_source_state_async(
                conn,
                source_id=source.source_id,
                workspace_id=source.workspace_id,
            )

        await self._run_async_write(
            operation_name="create_source_async",
            operation=operation,
        )
        return await self.require_source_async(source.source_id)

    async def bootstrap_todo_sources_async(
        self,
        *,
        workspace_id: str,
        source: BoardTodoSource | None,
    ) -> bool:
        async def operation(conn: aiosqlite.Connection) -> bool:
            await _ensure_workspace_state_async(conn, workspace_id)
            state_row = await async_fetchone(
                conn,
                """
                SELECT todo_sources_bootstrapped
                FROM board_todo_workspace_state
                WHERE workspace_id=?
                """,
                (workspace_id,),
            )
            if state_row is not None and bool(
                _int_or_none(state_row["todo_sources_bootstrapped"]) or 0
            ):
                return False
            existing_source = await async_fetchone(
                conn,
                """
                SELECT source_id
                FROM board_todo_sources
                WHERE workspace_id=? AND kind=?
                LIMIT 1
                """,
                (workspace_id, BoardTodoSourceKind.GITHUB_ISSUES.value),
            )
            created = False
            if existing_source is None and source is not None:
                await conn.execute(
                    """
                    INSERT INTO board_todo_sources (
                        source_id,
                        workspace_id,
                        kind,
                        provider,
                        display_name,
                        enabled,
                        repository_full_name,
                        system_managed,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _source_params(source),
                )
                await _ensure_source_state_async(
                    conn,
                    source_id=source.source_id,
                    workspace_id=source.workspace_id,
                )
                created = True
            await conn.execute(
                """
                UPDATE board_todo_workspace_state
                SET todo_sources_bootstrapped=1, updated_at=?
                WHERE workspace_id=?
                """,
                (_datetime_to_text(datetime.now().astimezone()), workspace_id),
            )
            return created

        return await self._run_async_write(
            operation_name="bootstrap_todo_sources_async",
            operation=operation,
        )

    async def upsert_system_source_async(
        self, source: BoardTodoSource
    ) -> BoardTodoSource:
        async def operation(conn: aiosqlite.Connection) -> None:
            await conn.execute(
                """
                INSERT INTO board_todo_sources (
                    source_id,
                    workspace_id,
                    kind,
                    provider,
                    display_name,
                    enabled,
                    repository_full_name,
                    system_managed,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    workspace_id=excluded.workspace_id,
                    kind=excluded.kind,
                    provider=excluded.provider,
                    display_name=excluded.display_name,
                    enabled=excluded.enabled,
                    repository_full_name=excluded.repository_full_name,
                    system_managed=excluded.system_managed,
                    updated_at=excluded.updated_at
                """,
                _source_params(source),
            )
            await _ensure_source_state_async(
                conn,
                source_id=source.source_id,
                workspace_id=source.workspace_id,
            )

        await self._run_async_write(
            operation_name="upsert_system_source_async",
            operation=operation,
        )
        return await self.require_source_async(source.source_id)

    async def update_source_async(self, source: BoardTodoSource) -> BoardTodoSource:
        next_source = source.model_copy(
            update={"updated_at": datetime.now().astimezone()}
        )

        async def operation(conn: aiosqlite.Connection) -> None:
            await _ensure_unique_github_source_repository_async(
                conn,
                source=next_source,
                excluded_source_id=next_source.source_id,
            )
            cursor = await conn.execute(
                """
                UPDATE board_todo_sources
                SET
                    workspace_id=?,
                    kind=?,
                    provider=?,
                    display_name=?,
                    enabled=?,
                    repository_full_name=?,
                    system_managed=?,
                    created_at=?,
                    updated_at=?
                WHERE source_id=?
                """,
                (*_source_params(next_source)[1:], next_source.source_id),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown board todo source: {source.source_id}")

        await self._run_async_write(
            operation_name="update_source_async",
            operation=operation,
        )
        return await self.require_source_async(source.source_id)

    async def delete_source_async(self, source_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                "DELETE FROM board_todo_sources WHERE source_id=?",
                (source_id,),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown board todo source: {source_id}")
            await conn.execute(
                "DELETE FROM board_todo_source_state WHERE source_id=?",
                (source_id,),
            )

        await self._run_async_write(
            operation_name="delete_source_async",
            operation=operation,
        )

    async def list_sources_async(
        self, *, workspace_id: str
    ) -> tuple[BoardTodoSource, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_sources
                WHERE workspace_id=?
                ORDER BY system_managed DESC, created_at ASC
                """,
                (workspace_id,),
            )
        )
        return tuple(source for row in rows if (source := _row_to_source_or_none(row)))

    async def find_source_by_repository_async(
        self,
        *,
        workspace_id: str,
        repository_full_name: str,
    ) -> BoardTodoSource | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_sources
                WHERE workspace_id=?
                    AND kind=?
                    AND repository_full_name=?
                """,
                (
                    workspace_id,
                    BoardTodoSourceKind.GITHUB_ISSUES.value,
                    repository_full_name,
                ),
            )
        )
        return _row_to_source_or_none(row)

    async def require_source_async(self, source_id: str) -> BoardTodoSource:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM board_todo_sources WHERE source_id=?",
                (source_id,),
            )
        )
        source = _row_to_source_or_none(row)
        if source is None:
            raise KeyError(f"Unknown board todo source: {source_id}")
        return source

    async def list_source_states_async(
        self, *, workspace_id: str
    ) -> tuple[BoardTodoSourceState, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_source_state
                WHERE workspace_id=?
                """,
                (workspace_id,),
            )
        )
        return tuple(
            state for row in rows if (state := _row_to_source_state_or_none(row))
        )

    async def get_source_state_async(
        self, *, source_id: str
    ) -> BoardTodoSourceState | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM board_todo_source_state WHERE source_id=?",
                (source_id,),
            )
        )
        return _row_to_source_state_or_none(row)

    async def update_source_sync_state_async(
        self,
        *,
        source_id: str,
        workspace_id: str,
        sync_cursor: datetime | None,
        status: BoardTodoSyncStatus,
        diagnostics: tuple[str, ...],
        started_at: datetime | None,
        finished_at: datetime | None,
    ) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            await _ensure_source_state_async(
                conn,
                source_id=source_id,
                workspace_id=workspace_id,
            )
            await conn.execute(
                """
                UPDATE board_todo_source_state
                SET
                    sync_cursor=?,
                    last_sync_started_at=?,
                    last_sync_finished_at=?,
                    last_sync_status=?,
                    last_diagnostics_json=?
                WHERE source_id=?
                """,
                (
                    _datetime_to_text(sync_cursor),
                    _datetime_to_text(started_at),
                    _datetime_to_text(finished_at),
                    status.value,
                    json.dumps(list(diagnostics)),
                    source_id,
                ),
            )

        await self._run_async_write(
            operation_name="update_source_sync_state_async",
            operation=operation,
        )

    async def count_items_for_source_async(self, *, source_id: str) -> int:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT COUNT(*) AS item_count FROM board_todo_items WHERE source_id=?",
                (source_id,),
            )
        )
        if row is None:
            return 0
        return _int_or_none(row["item_count"]) or 0

    async def count_items_for_source_identity_async(
        self,
        *,
        source: BoardTodoSource,
    ) -> int:
        if source.repository_full_name is None:
            return await self.count_items_for_source_async(source_id=source.source_id)
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT COUNT(*) AS item_count
                FROM board_todo_items
                WHERE source_id=?
                   OR (
                        source_id IS NULL
                        AND workspace_id=?
                        AND source_provider=?
                        AND LOWER(repository_full_name)=LOWER(?)
                   )
                """,
                (
                    source.source_id,
                    source.workspace_id,
                    source.provider.value,
                    source.repository_full_name,
                ),
            )
        )
        if row is None:
            return 0
        return _int_or_none(row["item_count"]) or 0

    async def upsert_source_async(self, item: BoardTodoItem) -> BoardTodoItem:
        async def operation(conn: aiosqlite.Connection) -> None:
            next_item = item.model_copy(
                update={
                    "item_revision": await _next_workspace_revision_async(
                        conn,
                        item.workspace_id,
                    )
                }
            )
            await _canonicalize_legacy_github_issue_source_key_async(
                conn,
                item=next_item,
            )
            cursor = await conn.execute(
                """
                INSERT INTO board_todo_items (
                    todo_id,
                    workspace_id,
                    source_id,
                    status,
                    title,
                    body,
                    source_provider,
                    source_type,
                    source_key,
                    repository_full_name,
                    issue_number,
                    pull_request_number,
                    html_url,
                    session_id,
                    run_id,
                    current_attempt_id,
                    active_attempt_id,
                    execution_workspace_id,
                    execution_policy,
                    runtime_target_kind,
                    runtime_target_id,
                    queue_ticket_id,
                    linked_pr_number,
                    linked_pr_url,
                    archived_at,
                    last_synced_at,
                    source_updated_at,
                    last_status_reason,
                    item_revision,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, source_provider, source_key)
                DO UPDATE SET
                    status=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN excluded.status
                        ELSE board_todo_items.status
                    END,
                    title=excluded.title,
                    body=excluded.body,
                    source_id=COALESCE(excluded.source_id, board_todo_items.source_id),
                    source_type=excluded.source_type,
                    repository_full_name=excluded.repository_full_name,
                    issue_number=excluded.issue_number,
                    pull_request_number=excluded.pull_request_number,
                    html_url=excluded.html_url,
                    session_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.session_id
                    END,
                    run_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.run_id
                    END,
                    current_attempt_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.current_attempt_id
                    END,
                    active_attempt_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.active_attempt_id
                    END,
                    execution_workspace_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.execution_workspace_id
                    END,
                    execution_policy=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.execution_policy
                    END,
                    runtime_target_kind=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.runtime_target_kind
                    END,
                    runtime_target_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.runtime_target_id
                    END,
                    queue_ticket_id=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.queue_ticket_id
                    END,
                    linked_pr_number=COALESCE(
                        board_todo_items.linked_pr_number,
                        excluded.linked_pr_number
                    ),
                    linked_pr_url=COALESCE(
                        board_todo_items.linked_pr_url,
                        excluded.linked_pr_url
                    ),
                    archived_at=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN NULL
                        ELSE board_todo_items.archived_at
                    END,
                    last_synced_at=excluded.last_synced_at,
                    source_updated_at=excluded.source_updated_at,
                    last_status_reason=CASE
                        WHEN board_todo_items.source_provider='github'
                            AND board_todo_items.source_type='github_issue'
                            AND board_todo_items.status='archived'
                            AND board_todo_items.last_status_reason IN (
                                'GitHub issue closed',
                                'GitHub issue no longer active',
                                'GitHub issue no longer open'
                            )
                        THEN 'GitHub issue reopened'
                        ELSE board_todo_items.last_status_reason
                    END,
                    item_revision=excluded.item_revision,
                    updated_at=excluded.updated_at
                """,
                _item_params(next_item),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="upsert_source_async",
            operation=operation,
        )
        return await self.require_by_source_async(
            workspace_id=item.workspace_id,
            source_provider=item.source_provider,
            source_key=item.source_key,
        )

    async def get_async(self, todo_id: str) -> BoardTodoItem | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT * FROM board_todo_items WHERE todo_id=?",
                (todo_id,),
            )
        )
        return _row_to_item_or_none(row)

    async def require_async(self, todo_id: str) -> BoardTodoItem:
        item = await self.get_async(todo_id)
        if item is None:
            raise KeyError(f"Unknown board todo item: {todo_id}")
        return item

    async def require_by_source_async(
        self,
        *,
        workspace_id: str,
        source_provider: BoardTodoSourceProvider,
        source_key: str,
    ) -> BoardTodoItem:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT *
                FROM board_todo_items
                WHERE workspace_id=? AND source_provider=? AND source_key=?
                """,
                (workspace_id, source_provider.value, source_key),
            )
        )
        item = _row_to_item_or_none(row)
        if item is None:
            raise KeyError(f"Unknown board todo source: {source_key}")
        return item

    async def list_by_workspace_async(
        self,
        *,
        workspace_id: str,
        include_archived: bool = False,
    ) -> tuple[BoardTodoItem, ...]:
        if include_archived:
            rows = await self._run_async_read(
                lambda conn: async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM board_todo_items
                    WHERE workspace_id=?
                    ORDER BY updated_at DESC
                    """,
                    (workspace_id,),
                )
            )
        else:
            rows = await self._run_async_read(
                lambda conn: async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM board_todo_items
                    WHERE workspace_id=? AND status<>?
                    ORDER BY updated_at DESC
                    """,
                    (workspace_id, BoardTodoStatus.ARCHIVED.value),
                )
            )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def list_delta_async(
        self,
        *,
        workspace_id: str,
        after_revision: int,
        include_archived: bool = False,
    ) -> tuple[BoardTodoItem, ...]:
        if include_archived:
            rows = await self._run_async_read(
                lambda conn: async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM board_todo_items
                    WHERE workspace_id=? AND item_revision>?
                    ORDER BY updated_at DESC
                    """,
                    (workspace_id, after_revision),
                )
            )
        else:
            rows = await self._run_async_read(
                lambda conn: async_fetchall(
                    conn,
                    """
                    SELECT *
                    FROM board_todo_items
                    WHERE workspace_id=? AND item_revision>? AND status<>?
                    ORDER BY updated_at DESC
                    """,
                    (
                        workspace_id,
                        after_revision,
                        BoardTodoStatus.ARCHIVED.value,
                    ),
                )
            )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def list_removed_from_active_since_async(
        self,
        *,
        workspace_id: str,
        after_revision: int,
    ) -> tuple[str, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT todo_id
                FROM board_todo_items
                WHERE workspace_id=? AND item_revision>? AND status=?
                ORDER BY updated_at DESC
                """,
                (workspace_id, after_revision, BoardTodoStatus.ARCHIVED.value),
            )
        )
        return tuple(
            require_persisted_identifier(row["todo_id"], field_name="todo_id")
            for row in rows
        )

    async def get_workspace_revision_async(self, workspace_id: str) -> int:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT revision
                FROM board_todo_workspace_state
                WHERE workspace_id=?
                """,
                (workspace_id,),
            )
        )
        if row is None:
            return 0
        return _int_or_none(row["revision"]) or 0

    async def get_todo_sources_bootstrapped_async(self, workspace_id: str) -> bool:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT todo_sources_bootstrapped
                FROM board_todo_workspace_state
                WHERE workspace_id=?
                """,
                (workspace_id,),
            )
        )
        if row is None:
            return False
        return bool(_int_or_none(row["todo_sources_bootstrapped"]) or 0)

    async def mark_todo_sources_bootstrapped_async(self, workspace_id: str) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            await _ensure_workspace_state_async(conn, workspace_id)
            await conn.execute(
                """
                UPDATE board_todo_workspace_state
                SET todo_sources_bootstrapped=1, updated_at=?
                WHERE workspace_id=?
                """,
                (_datetime_to_text(datetime.now().astimezone()), workspace_id),
            )

        await self._run_async_write(
            operation_name="mark_todo_sources_bootstrapped_async",
            operation=operation,
        )

    async def get_github_issue_sync_cursor_async(
        self,
        *,
        workspace_id: str,
        repository_full_name: str,
    ) -> datetime | None:
        row = await self._run_async_read(
            lambda conn: async_fetchone(
                conn,
                """
                SELECT github_issue_sync_cursor, repository_full_name
                FROM board_todo_workspace_state
                WHERE workspace_id=?
                """,
                (workspace_id,),
            )
        )
        if row is None:
            return None
        persisted_repository = normalize_persisted_text(row["repository_full_name"])
        if persisted_repository and persisted_repository != repository_full_name:
            return None
        return parse_persisted_datetime_or_none(row["github_issue_sync_cursor"])

    async def update_github_issue_sync_cursor_async(
        self,
        *,
        workspace_id: str,
        repository_full_name: str,
        cursor: datetime,
    ) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            await _ensure_workspace_state_async(conn, workspace_id)
            await conn.execute(
                """
                UPDATE board_todo_workspace_state
                SET
                    github_issue_sync_cursor=?,
                    repository_full_name=?,
                    updated_at=?
                WHERE workspace_id=?
                """,
                (
                    _datetime_to_text(cursor),
                    repository_full_name,
                    _datetime_to_text(datetime.now().astimezone()),
                    workspace_id,
                ),
            )

        await self._run_async_write(
            operation_name="update_github_issue_sync_cursor_async",
            operation=operation,
        )

    async def list_in_progress_async(self) -> tuple[BoardTodoItem, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_items
                WHERE status=? AND run_id IS NOT NULL
                ORDER BY updated_at DESC
                """,
                (BoardTodoStatus.IN_PROGRESS.value,),
            )
        )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def list_by_linked_pull_request_async(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
    ) -> tuple[BoardTodoItem, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_items
                WHERE LOWER(repository_full_name)=LOWER(?)
                    AND linked_pr_number=?
                    AND status<>?
                ORDER BY updated_at DESC
                """,
                (
                    repository_full_name,
                    pull_request_number,
                    BoardTodoStatus.ARCHIVED.value,
                ),
            )
        )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def list_by_session_async(
        self, *, session_id: str
    ) -> tuple[BoardTodoItem, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_items
                WHERE session_id=?
                ORDER BY updated_at DESC
                """,
                (session_id,),
            )
        )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def list_active_github_issue_items_async(
        self,
        *,
        workspace_id: str,
        repository_full_name: str,
    ) -> tuple[BoardTodoItem, ...]:
        rows = await self._run_async_read(
            lambda conn: async_fetchall(
                conn,
                """
                SELECT *
                FROM board_todo_items
                WHERE workspace_id=?
                    AND LOWER(repository_full_name)=LOWER(?)
                    AND source_provider=?
                    AND source_type=?
                    AND status<>?
                ORDER BY updated_at DESC
                """,
                (
                    workspace_id,
                    repository_full_name,
                    BoardTodoSourceProvider.GITHUB.value,
                    BoardTodoSourceType.GITHUB_ISSUE.value,
                    BoardTodoStatus.ARCHIVED.value,
                ),
            )
        )
        return tuple(item for row in rows if (item := _row_to_item_or_none(row)))

    async def update_async(self, item: BoardTodoItem) -> BoardTodoItem:
        next_item = item.model_copy(update={"updated_at": datetime.now().astimezone()})

        async def operation(conn: aiosqlite.Connection) -> None:
            revisioned_item = next_item.model_copy(
                update={
                    "item_revision": await _next_workspace_revision_async(
                        conn,
                        next_item.workspace_id,
                    )
                }
            )
            cursor = await conn.execute(
                """
                UPDATE board_todo_items
                SET
                    workspace_id=?,
                    source_id=?,
                    status=?,
                    title=?,
                    body=?,
                    source_provider=?,
                    source_type=?,
                    source_key=?,
                    repository_full_name=?,
                    issue_number=?,
                    pull_request_number=?,
                    html_url=?,
                    session_id=?,
                    run_id=?,
                    current_attempt_id=?,
                    active_attempt_id=?,
                    execution_workspace_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    queue_ticket_id=?,
                    linked_pr_number=?,
                    linked_pr_url=?,
                    archived_at=?,
                    last_synced_at=?,
                    source_updated_at=?,
                    last_status_reason=?,
                    item_revision=?,
                    created_at=?,
                    updated_at=?
                WHERE todo_id=?
                """,
                (*_item_params(revisioned_item)[1:], revisioned_item.todo_id),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown board todo item: {next_item.todo_id}")

        await self._run_async_write(
            operation_name="update_async",
            operation=operation,
        )
        return await self.require_async(next_item.todo_id)

    async def reserve_start_async(self, item: BoardTodoItem) -> BoardTodoItem:
        next_item = item.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "session_id": None,
                "run_id": None,
                "last_status_reason": "Starting from board todo item",
                "updated_at": datetime.now().astimezone(),
            }
        )

        async def operation(conn: aiosqlite.Connection) -> None:
            revisioned_item = next_item.model_copy(
                update={
                    "item_revision": await _next_workspace_revision_async(
                        conn,
                        next_item.workspace_id,
                    )
                }
            )
            cursor = await conn.execute(
                """
                UPDATE board_todo_items
                SET
                    workspace_id=?,
                    source_id=?,
                    status=?,
                    title=?,
                    body=?,
                    source_provider=?,
                    source_type=?,
                    source_key=?,
                    repository_full_name=?,
                    issue_number=?,
                    pull_request_number=?,
                    html_url=?,
                    session_id=?,
                    run_id=?,
                    current_attempt_id=?,
                    active_attempt_id=?,
                    execution_workspace_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    queue_ticket_id=?,
                    linked_pr_number=?,
                    linked_pr_url=?,
                    archived_at=?,
                    last_synced_at=?,
                    source_updated_at=?,
                    last_status_reason=?,
                    item_revision=?,
                    created_at=?,
                    updated_at=?
                WHERE todo_id=? AND status=?
                """,
                (
                    *_item_params(revisioned_item)[1:],
                    revisioned_item.todo_id,
                    BoardTodoStatus.TODO.value,
                ),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise ValueError("only todo board items can be started")

        await self._run_async_write(
            operation_name="reserve_start_async",
            operation=operation,
        )
        return await self.require_async(next_item.todo_id)

    async def reserve_request_changes_async(
        self,
        item: BoardTodoItem,
    ) -> BoardTodoItem:
        next_item = item.model_copy(
            update={
                "status": BoardTodoStatus.IN_PROGRESS,
                "run_id": None,
                "last_status_reason": "Requesting changes",
                "updated_at": datetime.now().astimezone(),
            }
        )

        async def operation(conn: aiosqlite.Connection) -> None:
            revisioned_item = next_item.model_copy(
                update={
                    "item_revision": await _next_workspace_revision_async(
                        conn,
                        next_item.workspace_id,
                    )
                }
            )
            cursor = await conn.execute(
                """
                UPDATE board_todo_items
                SET
                    workspace_id=?,
                    source_id=?,
                    status=?,
                    title=?,
                    body=?,
                    source_provider=?,
                    source_type=?,
                    source_key=?,
                    repository_full_name=?,
                    issue_number=?,
                    pull_request_number=?,
                    html_url=?,
                    session_id=?,
                    run_id=?,
                    current_attempt_id=?,
                    active_attempt_id=?,
                    execution_workspace_id=?,
                    execution_policy=?,
                    runtime_target_kind=?,
                    runtime_target_id=?,
                    queue_ticket_id=?,
                    linked_pr_number=?,
                    linked_pr_url=?,
                    archived_at=?,
                    last_synced_at=?,
                    source_updated_at=?,
                    last_status_reason=?,
                    item_revision=?,
                    created_at=?,
                    updated_at=?
                WHERE todo_id=? AND status=?
                """,
                (
                    *_item_params(revisioned_item)[1:],
                    revisioned_item.todo_id,
                    BoardTodoStatus.REVIEW.value,
                ),
            )
            await cursor.close()
            if cursor.rowcount == 0:
                raise ValueError("only review board items can request changes")

        await self._run_async_write(
            operation_name="reserve_request_changes_async",
            operation=operation,
        )
        return await self.require_async(next_item.todo_id)

    async def mark_pull_request_done_async(
        self,
        *,
        repository_full_name: str,
        pull_request_number: int,
        reason: str,
    ) -> tuple[BoardTodoItem, ...]:
        items = await self.list_by_linked_pull_request_async(
            repository_full_name=repository_full_name,
            pull_request_number=pull_request_number,
        )
        updated: list[BoardTodoItem] = []
        for item in items:
            if item.status == BoardTodoStatus.ARCHIVED:
                continue
            updated.append(
                await self.update_async(
                    item.model_copy(
                        update={
                            "status": BoardTodoStatus.DONE,
                            "last_status_reason": reason,
                        }
                    )
                )
            )
        return tuple(updated)

    async def _insert_async(self, item: BoardTodoItem) -> None:
        async def operation(conn: aiosqlite.Connection) -> None:
            next_item = item.model_copy(
                update={
                    "item_revision": await _next_workspace_revision_async(
                        conn,
                        item.workspace_id,
                    )
                }
            )
            cursor = await conn.execute(
                """
                INSERT INTO board_todo_items (
                    todo_id,
                    workspace_id,
                    source_id,
                    status,
                    title,
                    body,
                    source_provider,
                    source_type,
                    source_key,
                    repository_full_name,
                    issue_number,
                    pull_request_number,
                    html_url,
                    session_id,
                    run_id,
                    current_attempt_id,
                    active_attempt_id,
                    execution_workspace_id,
                    execution_policy,
                    runtime_target_kind,
                    runtime_target_id,
                    queue_ticket_id,
                    linked_pr_number,
                    linked_pr_url,
                    archived_at,
                    last_synced_at,
                    source_updated_at,
                    last_status_reason,
                    item_revision,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _item_params(next_item),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="insert_async",
            operation=operation,
        )


def _item_params(item: BoardTodoItem) -> tuple[object, ...]:
    return (
        item.todo_id,
        item.workspace_id,
        item.source_id,
        item.status.value,
        item.title,
        item.body,
        item.source_provider.value,
        item.source_type.value,
        item.source_key,
        item.repository_full_name,
        item.issue_number,
        item.pull_request_number,
        item.html_url,
        item.session_id,
        item.run_id,
        item.current_attempt_id,
        item.active_attempt_id,
        item.execution_workspace_id,
        item.execution_policy.value if item.execution_policy is not None else None,
        item.runtime_target_kind.value
        if item.runtime_target_kind is not None
        else None,
        item.runtime_target_id,
        item.queue_ticket_id,
        item.linked_pr_number,
        item.linked_pr_url,
        _datetime_to_text(item.archived_at),
        _datetime_to_text(item.last_synced_at),
        _datetime_to_text(item.source_updated_at),
        item.last_status_reason,
        item.item_revision,
        _datetime_to_text(item.created_at),
        _datetime_to_text(item.updated_at),
    )


def _source_params(source: BoardTodoSource) -> tuple[object, ...]:
    return (
        source.source_id,
        source.workspace_id,
        source.kind.value,
        source.provider.value,
        source.display_name,
        1 if source.enabled else 0,
        source.repository_full_name,
        1 if source.system_managed else 0,
        _datetime_to_text(source.created_at),
        _datetime_to_text(source.updated_at),
    )


def _attempt_params(attempt: BoardTodoAttempt) -> tuple[object, ...]:
    return (
        attempt.attempt_id,
        attempt.todo_id,
        attempt.attempt_type.value,
        attempt.status.value,
        attempt.board_workspace_id,
        attempt.initiated_from_workspace_id,
        attempt.source_workspace_id,
        attempt.execution_workspace_id,
        attempt.execution_policy.value
        if attempt.execution_policy is not None
        else None,
        (
            attempt.runtime_target_kind.value
            if attempt.runtime_target_kind is not None
            else None
        ),
        attempt.runtime_target_id,
        attempt.queue_ticket_id,
        attempt.handoff_initiator,
        attempt.start_policy,
        1 if attempt.yolo else 0,
        json.dumps(attempt.thinking.model_dump(mode="json")),
        attempt.session_id,
        attempt.run_id,
        attempt.prompt_ref,
        attempt.summary,
        attempt.error,
        _datetime_to_text(attempt.created_at),
        _datetime_to_text(attempt.started_at),
        _datetime_to_text(attempt.finished_at),
    )


def _handoff_prompt_params(prompt: BoardTodoHandoffPrompt) -> tuple[object, ...]:
    return (
        prompt.prompt_ref,
        prompt.todo_id,
        prompt.attempt_id,
        prompt.template_kind,
        prompt.template_source,
        prompt.final_prompt_snapshot,
        _datetime_to_text(prompt.created_at),
    )


def _queue_ticket_params(ticket: BoardTodoExecutionQueueTicket) -> tuple[object, ...]:
    return (
        ticket.queue_ticket_id,
        ticket.todo_id,
        ticket.attempt_id,
        ticket.prompt_ref,
        ticket.queue_kind.value,
        ticket.status.value,
        ticket.board_workspace_id,
        ticket.source_workspace_id,
        ticket.initiated_from_workspace_id,
        ticket.execution_workspace_id,
        ticket.previous_run_id,
        ticket.execution_policy.value,
        (
            ticket.runtime_target_kind.value
            if ticket.runtime_target_kind is not None
            else None
        ),
        ticket.runtime_target_id,
        ticket.session_mode.value if ticket.session_mode is not None else None,
        ticket.normal_root_role_id,
        ticket.orchestration_preset_id,
        1 if ticket.yolo else 0,
        json.dumps(ticket.thinking.model_dump(mode="json")),
        ticket.claim_token,
        _datetime_to_text(ticket.claim_expires_at),
        ticket.claimed_by,
        ticket.failure_count,
        json.dumps(list(ticket.diagnostics)),
        _datetime_to_text(ticket.created_at),
        _datetime_to_text(ticket.updated_at),
    )


def _handoff_template_params(template: BoardTodoHandoffTemplate) -> tuple[object, ...]:
    return (
        template.template_id,
        template.workspace_id,
        template.scope.value,
        template.template_kind.value,
        template.source_id,
        template.template,
        _datetime_to_text(template.created_at),
        _datetime_to_text(template.updated_at),
    )


def _diagnostic_params(diagnostic: BoardTodoDiagnostic) -> tuple[object, ...]:
    return (
        diagnostic.diagnostic_id,
        diagnostic.todo_id,
        diagnostic.workspace_id,
        diagnostic.kind,
        diagnostic.message,
        diagnostic.attempt_id,
        diagnostic.queue_ticket_id,
        _datetime_to_text(diagnostic.created_at),
    )


def _row_to_item_or_none(row: sqlite3.Row | None) -> BoardTodoItem | None:
    if row is None:
        return None
    try:
        return BoardTodoItem(
            todo_id=require_persisted_identifier(
                row["todo_id"],
                field_name="todo_id",
            ),
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            source_id=normalize_persisted_text(row["source_id"]) or None,
            status=BoardTodoStatus(str(row["status"])),
            title=normalize_persisted_text(row["title"]) or "",
            body=normalize_persisted_text(row["body"]) or "",
            source_provider=BoardTodoSourceProvider(str(row["source_provider"])),
            source_type=BoardTodoSourceType(str(row["source_type"])),
            source_key=normalize_persisted_text(row["source_key"]) or "",
            repository_full_name=normalize_persisted_text(row["repository_full_name"])
            or None,
            issue_number=_int_or_none(row["issue_number"]),
            pull_request_number=_int_or_none(row["pull_request_number"]),
            html_url=normalize_persisted_text(row["html_url"]) or None,
            session_id=normalize_persisted_text(row["session_id"]) or None,
            run_id=normalize_persisted_text(row["run_id"]) or None,
            current_attempt_id=normalize_persisted_text(row["current_attempt_id"])
            or None,
            active_attempt_id=normalize_persisted_text(row["active_attempt_id"])
            or None,
            execution_workspace_id=normalize_persisted_text(
                row["execution_workspace_id"]
            )
            or None,
            execution_policy=_execution_policy_or_none(row["execution_policy"]),
            runtime_target_kind=_runtime_target_kind_or_none(
                row["runtime_target_kind"]
            ),
            runtime_target_id=normalize_persisted_text(row["runtime_target_id"])
            or None,
            queue_ticket_id=normalize_persisted_text(row["queue_ticket_id"]) or None,
            linked_pr_number=_int_or_none(row["linked_pr_number"]),
            linked_pr_url=normalize_persisted_text(row["linked_pr_url"]) or None,
            archived_at=parse_persisted_datetime_or_none(row["archived_at"]),
            last_synced_at=parse_persisted_datetime_or_none(row["last_synced_at"]),
            source_updated_at=parse_persisted_datetime_or_none(
                row["source_updated_at"]
            ),
            last_status_reason=normalize_persisted_text(row["last_status_reason"])
            or None,
            item_revision=_int_or_none(row["item_revision"]) or 0,
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
            updated_at=parse_persisted_datetime_or_none(row["updated_at"])
            or datetime.now().astimezone(),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning("Skipping invalid board todo row %s: %s", row["todo_id"], exc)
        return None


def _row_to_attempt_or_none(row: sqlite3.Row | None) -> BoardTodoAttempt | None:
    if row is None:
        return None
    try:
        return BoardTodoAttempt(
            attempt_id=require_persisted_identifier(
                row["attempt_id"],
                field_name="attempt_id",
            ),
            todo_id=require_persisted_identifier(
                row["todo_id"],
                field_name="todo_id",
            ),
            attempt_type=BoardTodoAttemptType(str(row["attempt_type"])),
            status=BoardTodoAttemptStatus(str(row["status"])),
            board_workspace_id=normalize_persisted_text(row["board_workspace_id"])
            or None,
            initiated_from_workspace_id=normalize_persisted_text(
                row["initiated_from_workspace_id"]
            )
            or None,
            source_workspace_id=normalize_persisted_text(row["source_workspace_id"])
            or None,
            execution_workspace_id=normalize_persisted_text(
                row["execution_workspace_id"]
            )
            or None,
            execution_policy=_execution_policy_or_none(row["execution_policy"]),
            runtime_target_kind=_runtime_target_kind_or_none(
                row["runtime_target_kind"]
            ),
            runtime_target_id=normalize_persisted_text(row["runtime_target_id"])
            or None,
            queue_ticket_id=normalize_persisted_text(row["queue_ticket_id"]) or None,
            handoff_initiator=normalize_persisted_text(row["handoff_initiator"])
            or "human",
            start_policy=normalize_persisted_text(row["start_policy"])
            or "human_required",
            yolo=bool(_int_or_none(row["yolo"]) if row["yolo"] is not None else 1),
            thinking=_thinking_from_json(
                normalize_persisted_text(row["thinking_json"]) or "{}"
            ),
            session_id=normalize_persisted_text(row["session_id"]) or None,
            run_id=normalize_persisted_text(row["run_id"]) or None,
            prompt_ref=normalize_persisted_text(row["prompt_ref"]) or None,
            summary=normalize_persisted_text(row["summary"]) or None,
            error=normalize_persisted_text(row["error"]) or None,
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
            started_at=parse_persisted_datetime_or_none(row["started_at"]),
            finished_at=parse_persisted_datetime_or_none(row["finished_at"]),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo attempt row %s: %s",
            row["attempt_id"],
            exc,
        )
        return None


def _row_to_handoff_prompt_or_none(
    row: sqlite3.Row | None,
) -> BoardTodoHandoffPrompt | None:
    if row is None:
        return None
    try:
        return BoardTodoHandoffPrompt(
            prompt_ref=require_persisted_identifier(
                row["prompt_ref"],
                field_name="prompt_ref",
            ),
            todo_id=require_persisted_identifier(
                row["todo_id"],
                field_name="todo_id",
            ),
            attempt_id=require_persisted_identifier(
                row["attempt_id"],
                field_name="attempt_id",
            ),
            template_kind=normalize_persisted_text(row["template_kind"]) or "",
            template_source=normalize_persisted_text(row["template_source"]) or "",
            final_prompt_snapshot=normalize_persisted_text(row["final_prompt_snapshot"])
            or "",
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo handoff prompt row %s: %s",
            row["prompt_ref"],
            exc,
        )
        return None


def _row_to_queue_ticket_or_none(
    row: sqlite3.Row | None,
) -> BoardTodoExecutionQueueTicket | None:
    if row is None:
        return None
    try:
        return BoardTodoExecutionQueueTicket(
            queue_ticket_id=require_persisted_identifier(
                row["queue_ticket_id"],
                field_name="queue_ticket_id",
            ),
            todo_id=require_persisted_identifier(row["todo_id"], field_name="todo_id"),
            attempt_id=require_persisted_identifier(
                row["attempt_id"],
                field_name="attempt_id",
            ),
            prompt_ref=require_persisted_identifier(
                row["prompt_ref"],
                field_name="prompt_ref",
            ),
            queue_kind=BoardTodoQueueKind(str(row["queue_kind"])),
            status=BoardTodoQueueStatus(str(row["status"])),
            board_workspace_id=require_persisted_identifier(
                row["board_workspace_id"],
                field_name="board_workspace_id",
            ),
            source_workspace_id=require_persisted_identifier(
                row["source_workspace_id"],
                field_name="source_workspace_id",
            ),
            initiated_from_workspace_id=normalize_persisted_text(
                row["initiated_from_workspace_id"]
            )
            or None,
            execution_workspace_id=normalize_persisted_text(
                row["execution_workspace_id"]
            )
            or None,
            previous_run_id=normalize_persisted_text(row["previous_run_id"]) or None,
            execution_policy=BoardTodoExecutionPolicy(str(row["execution_policy"])),
            runtime_target_kind=_runtime_target_kind_or_none(
                row["runtime_target_kind"]
            ),
            runtime_target_id=normalize_persisted_text(row["runtime_target_id"])
            or None,
            session_mode=_session_mode_or_none(row["session_mode"]),
            normal_root_role_id=normalize_persisted_text(row["normal_root_role_id"])
            or None,
            orchestration_preset_id=normalize_persisted_text(
                row["orchestration_preset_id"]
            )
            or None,
            yolo=bool(_int_or_none(row["yolo"]) if row["yolo"] is not None else 1),
            thinking=_thinking_from_json(
                normalize_persisted_text(row["thinking_json"]) or "{}"
            ),
            claim_token=normalize_persisted_text(row["claim_token"]) or None,
            claim_expires_at=parse_persisted_datetime_or_none(row["claim_expires_at"]),
            claimed_by=normalize_persisted_text(row["claimed_by"]) or None,
            failure_count=_int_or_none(row["failure_count"]) or 0,
            diagnostics=_diagnostics_from_json(
                normalize_persisted_text(row["diagnostics_json"]) or "[]"
            ),
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
            updated_at=parse_persisted_datetime_or_none(row["updated_at"])
            or datetime.now().astimezone(),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo queue ticket row %s: %s",
            row["queue_ticket_id"],
            exc,
        )
        return None


def _row_to_handoff_template_or_none(
    row: sqlite3.Row | None,
) -> BoardTodoHandoffTemplate | None:
    if row is None:
        return None
    try:
        return BoardTodoHandoffTemplate(
            template_id=require_persisted_identifier(
                row["template_id"],
                field_name="template_id",
            ),
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            scope=BoardTodoTemplateScope(str(row["scope"])),
            template_kind=BoardTodoHandoffTemplateKind(str(row["template_kind"])),
            source_id=normalize_persisted_text(row["source_id"]) or None,
            template=normalize_persisted_text(row["template"]) or "",
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
            updated_at=parse_persisted_datetime_or_none(row["updated_at"])
            or datetime.now().astimezone(),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo handoff template row %s: %s",
            row["template_id"],
            exc,
        )
        return None


def _row_to_source_or_none(row: sqlite3.Row | None) -> BoardTodoSource | None:
    if row is None:
        return None
    try:
        return BoardTodoSource(
            source_id=require_persisted_identifier(
                row["source_id"],
                field_name="source_id",
            ),
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            kind=BoardTodoSourceKind(str(row["kind"])),
            provider=BoardTodoSourceProvider(str(row["provider"])),
            display_name=normalize_persisted_text(row["display_name"]) or "",
            enabled=bool(_int_or_none(row["enabled"]) or 0),
            repository_full_name=normalize_persisted_text(row["repository_full_name"])
            or None,
            system_managed=bool(_int_or_none(row["system_managed"]) or 0),
            created_at=parse_persisted_datetime_or_none(row["created_at"])
            or datetime.now().astimezone(),
            updated_at=parse_persisted_datetime_or_none(row["updated_at"])
            or datetime.now().astimezone(),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo source row %s: %s",
            row["source_id"],
            exc,
        )
        return None


def _row_to_source_state_or_none(
    row: sqlite3.Row | None,
) -> BoardTodoSourceState | None:
    if row is None:
        return None
    try:
        return BoardTodoSourceState(
            source_id=require_persisted_identifier(
                row["source_id"],
                field_name="source_id",
            ),
            workspace_id=require_persisted_identifier(
                row["workspace_id"],
                field_name="workspace_id",
            ),
            sync_cursor=parse_persisted_datetime_or_none(row["sync_cursor"]),
            last_sync_started_at=parse_persisted_datetime_or_none(
                row["last_sync_started_at"]
            ),
            last_sync_finished_at=parse_persisted_datetime_or_none(
                row["last_sync_finished_at"]
            ),
            last_sync_status=BoardTodoSyncStatus(str(row["last_sync_status"])),
            last_diagnostics=_diagnostics_from_json(
                normalize_persisted_text(row["last_diagnostics_json"]) or "[]"
            ),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        LOGGER.warning(
            "Skipping invalid board todo source state row %s: %s",
            row["source_id"],
            exc,
        )
        return None


def _diagnostics_from_json(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if str(item).strip())


def _thinking_from_json(value: str) -> RunThinkingConfig:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return RunThinkingConfig()
    if not isinstance(parsed, dict):
        return RunThinkingConfig()
    try:
        return RunThinkingConfig.model_validate(parsed)
    except ValidationError:
        return RunThinkingConfig()


def _execution_policy_or_none(value: object) -> BoardTodoExecutionPolicy | None:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return None
    try:
        return BoardTodoExecutionPolicy(normalized)
    except ValueError:
        return None


def _runtime_target_kind_or_none(value: object) -> BoardTodoRuntimeTargetKind | None:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return None
    try:
        return BoardTodoRuntimeTargetKind(normalized)
    except ValueError:
        return None


def _session_mode_or_none(value: object) -> SessionMode | None:
    normalized = normalize_persisted_text(value)
    if normalized is None:
        return None
    try:
        return SessionMode(normalized)
    except ValueError:
        return None


def _datetime_to_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _queue_ticket_is_pending_or_expired(
    ticket: BoardTodoExecutionQueueTicket,
    *,
    now: datetime,
) -> bool:
    if ticket.status == BoardTodoQueueStatus.PENDING:
        return True
    if ticket.status != BoardTodoQueueStatus.CLAIMED:
        return False
    if ticket.claim_expires_at is None:
        return False
    return ticket.claim_expires_at <= now


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _ensure_column(
    conn: BlockingAsyncSqliteConnection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in columns:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


async def _ensure_workspace_state_async(
    conn: aiosqlite.Connection,
    workspace_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO board_todo_workspace_state (
            workspace_id,
            revision,
            updated_at
        )
        VALUES (?, 0, ?)
        ON CONFLICT(workspace_id) DO NOTHING
        """,
        (workspace_id, _datetime_to_text(datetime.now().astimezone())),
    )


async def _ensure_source_state_async(
    conn: aiosqlite.Connection,
    *,
    source_id: str,
    workspace_id: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO board_todo_source_state (
            source_id,
            workspace_id,
            last_sync_status,
            last_diagnostics_json
        )
        VALUES (?, ?, 'idle', '[]')
        ON CONFLICT(source_id) DO NOTHING
        """,
        (source_id, workspace_id),
    )


async def _ensure_unique_github_source_repository_async(
    conn: aiosqlite.Connection,
    *,
    source: BoardTodoSource,
    excluded_source_id: str | None,
) -> None:
    if (
        source.kind != BoardTodoSourceKind.GITHUB_ISSUES
        or source.repository_full_name is None
    ):
        return
    row = await async_fetchone(
        conn,
        """
        SELECT source_id
        FROM board_todo_sources
        WHERE workspace_id=?
          AND kind=?
          AND repository_full_name IS NOT NULL
          AND LOWER(repository_full_name)=LOWER(?)
          AND (? IS NULL OR source_id<>?)
        LIMIT 1
        """,
        (
            source.workspace_id,
            BoardTodoSourceKind.GITHUB_ISSUES.value,
            source.repository_full_name,
            excluded_source_id,
            excluded_source_id,
        ),
    )
    if row is not None:
        raise ValueError("GitHub TODO source already exists for this repository")


async def _canonicalize_legacy_github_issue_source_key_async(
    conn: aiosqlite.Connection,
    *,
    item: BoardTodoItem,
) -> None:
    if (
        item.source_provider != BoardTodoSourceProvider.GITHUB
        or item.source_type != BoardTodoSourceType.GITHUB_ISSUE
        or item.repository_full_name is None
        or item.issue_number is None
    ):
        return
    legacy_row = await async_fetchone(
        conn,
        """
        SELECT todo_id, source_key
        FROM board_todo_items
        WHERE workspace_id=?
          AND source_provider=?
          AND source_type=?
          AND issue_number=?
          AND repository_full_name IS NOT NULL
          AND LOWER(repository_full_name)=LOWER(?)
        LIMIT 1
        """,
        (
            item.workspace_id,
            item.source_provider.value,
            item.source_type.value,
            item.issue_number,
            item.repository_full_name,
        ),
    )
    if legacy_row is None or legacy_row["source_key"] == item.source_key:
        return
    await conn.execute(
        """
        UPDATE board_todo_items
        SET source_key=?
        WHERE todo_id=?
          AND NOT EXISTS (
              SELECT 1
              FROM board_todo_items
              WHERE workspace_id=?
                AND source_provider=?
                AND source_key=?
          )
        """,
        (
            item.source_key,
            legacy_row["todo_id"],
            item.workspace_id,
            item.source_provider.value,
            item.source_key,
        ),
    )


async def _next_workspace_revision_async(
    conn: aiosqlite.Connection,
    workspace_id: str,
) -> int:
    await _ensure_workspace_state_async(conn, workspace_id)
    await conn.execute(
        """
        UPDATE board_todo_workspace_state
        SET revision=revision+1, updated_at=?
        WHERE workspace_id=?
        """,
        (_datetime_to_text(datetime.now().astimezone()), workspace_id),
    )
    cursor = await conn.execute(
        """
        SELECT revision
        FROM board_todo_workspace_state
        WHERE workspace_id=?
        """,
        (workspace_id,),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        return 0
    return _int_or_none(row["revision"]) or 0
