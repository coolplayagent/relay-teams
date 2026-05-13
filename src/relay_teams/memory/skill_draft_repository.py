# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

from relay_teams.memory.skill_draft_models import (
    MemorySkillDraft,
    MemorySkillDraftFile,
    MemorySkillDraftKind,
    MemorySkillDraftQuery,
    MemorySkillDraftQueryResult,
    MemorySkillDraftScopeKind,
    MemorySkillDraftStatus,
    MemorySkillDraftValidationMessage,
    draft_to_summary,
)
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """\
CREATE TABLE IF NOT EXISTS memory_skill_drafts (
    draft_id                 TEXT PRIMARY KEY,
    status                   TEXT NOT NULL,
    scope_kind               TEXT NOT NULL,
    workspace_id             TEXT,
    workspace_ids_json       TEXT NOT NULL DEFAULT '[]',
    source_memory_ids_json   TEXT NOT NULL DEFAULT '[]',
    draft_kind               TEXT NOT NULL,
    runtime_name             TEXT NOT NULL,
    description              TEXT NOT NULL DEFAULT '',
    instructions             TEXT NOT NULL DEFAULT '',
    files_json               TEXT NOT NULL DEFAULT '[]',
    validation_messages_json TEXT NOT NULL DEFAULT '[]',
    generation_error         TEXT NOT NULL DEFAULT '',
    applied_skill_id         TEXT,
    applied_ref              TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    validated_at             TEXT,
    applied_at               TEXT
)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_status_updated
    ON memory_skill_drafts(status, updated_at DESC)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_workspace_updated
    ON memory_skill_drafts(workspace_id, updated_at DESC)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_skill_drafts_kind_updated
    ON memory_skill_drafts(draft_kind, updated_at DESC)""",
)


class MemorySkillDraftRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._run_write(
            operation_name="init_memory_skill_draft_tables",
            operation=self._create_schema,
        )

    def _create_schema(self) -> None:
        for statement in _SCHEMA_STATEMENTS:
            self._conn.execute(statement)

    async def create_draft_async(self, draft: MemorySkillDraft) -> MemorySkillDraft:
        async def op(conn: aiosqlite.Connection) -> None:
            cursor = await conn.execute(
                """INSERT INTO memory_skill_drafts(
                    draft_id, status, scope_kind, workspace_id, workspace_ids_json,
                    source_memory_ids_json, draft_kind, runtime_name, description,
                    instructions, files_json, validation_messages_json,
                    generation_error, applied_skill_id, applied_ref, created_at,
                    updated_at, validated_at, applied_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                _draft_to_params(draft),
            )
            await cursor.close()

        await self._run_async_write(
            operation_name="create_memory_skill_draft_async",
            operation=op,
        )
        return draft

    async def get_draft_async(self, draft_id: str) -> MemorySkillDraft | None:
        async def op(conn: aiosqlite.Connection) -> MemorySkillDraft | None:
            row = await async_fetchone(
                conn,
                "SELECT * FROM memory_skill_drafts WHERE draft_id=?",
                (draft_id,),
            )
            if row is None:
                return None
            return _row_to_draft(row)

        return await self._run_async_read(op)

    async def update_draft_async(
        self,
        draft: MemorySkillDraft,
        *,
        expected_status: MemorySkillDraftStatus | None = None,
    ) -> MemorySkillDraft | None:
        async def op(conn: aiosqlite.Connection) -> bool:
            params: tuple[object, ...] = (*_draft_to_params(draft)[1:], draft.id)
            if expected_status is not None:
                params = (*params, expected_status.value)
                sql = """UPDATE memory_skill_drafts SET
                    status=?, scope_kind=?, workspace_id=?, workspace_ids_json=?,
                    source_memory_ids_json=?, draft_kind=?, runtime_name=?,
                    description=?, instructions=?, files_json=?,
                    validation_messages_json=?, generation_error=?,
                    applied_skill_id=?, applied_ref=?, created_at=?, updated_at=?,
                    validated_at=?, applied_at=?
                WHERE draft_id=? AND status=?"""
            else:
                sql = """UPDATE memory_skill_drafts SET
                    status=?, scope_kind=?, workspace_id=?, workspace_ids_json=?,
                    source_memory_ids_json=?, draft_kind=?, runtime_name=?,
                    description=?, instructions=?, files_json=?,
                    validation_messages_json=?, generation_error=?,
                    applied_skill_id=?, applied_ref=?, created_at=?, updated_at=?,
                    validated_at=?, applied_at=?
                WHERE draft_id=?"""
            cursor = await conn.execute(
                sql,
                params,
            )
            did_update_row = cursor.rowcount > 0
            await cursor.close()
            return did_update_row

        update_succeeded = await self._run_async_write(
            operation_name="update_memory_skill_draft_async",
            operation=op,
        )
        return draft if update_succeeded else None

    async def claim_draft_apply_async(
        self,
        *,
        draft_id: str,
        updated_at: datetime,
    ) -> MemorySkillDraft | None:
        async def op(conn: aiosqlite.Connection) -> MemorySkillDraft | None:
            cursor = await conn.execute(
                """UPDATE memory_skill_drafts
                SET status=?, updated_at=?
                WHERE draft_id=? AND status=?""",
                (
                    MemorySkillDraftStatus.APPLYING.value,
                    updated_at.isoformat(),
                    draft_id,
                    MemorySkillDraftStatus.VALIDATED.value,
                ),
            )
            changed = cursor.rowcount
            await cursor.close()
            if changed != 1:
                return None
            row = await async_fetchone(
                conn,
                "SELECT * FROM memory_skill_drafts WHERE draft_id=?",
                (draft_id,),
            )
            if row is None:
                return None
            return _row_to_draft(row)

        return await self._run_async_write(
            operation_name="claim_memory_skill_draft_apply_async",
            operation=op,
        )

    async def release_draft_apply_claim_async(
        self,
        *,
        draft_id: str,
        updated_at: datetime,
    ) -> bool:
        async def op(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                """UPDATE memory_skill_drafts
                SET status=?, updated_at=?
                WHERE draft_id=? AND status=?""",
                (
                    MemorySkillDraftStatus.VALIDATED.value,
                    updated_at.isoformat(),
                    draft_id,
                    MemorySkillDraftStatus.APPLYING.value,
                ),
            )
            changed = cursor.rowcount
            await cursor.close()
            return changed == 1

        return await self._run_async_write(
            operation_name="release_memory_skill_draft_apply_claim_async",
            operation=op,
        )

    async def complete_draft_apply_async(
        self,
        *,
        draft: MemorySkillDraft,
    ) -> MemorySkillDraft | None:
        async def op(conn: aiosqlite.Connection) -> MemorySkillDraft | None:
            cursor = await conn.execute(
                """UPDATE memory_skill_drafts SET
                    status=?, applied_skill_id=?, applied_ref=?, updated_at=?,
                    applied_at=?
                WHERE draft_id=? AND status=?""",
                (
                    MemorySkillDraftStatus.APPLIED.value,
                    draft.applied_skill_id,
                    draft.applied_ref,
                    draft.updated_at.isoformat(),
                    draft.applied_at.isoformat() if draft.applied_at else None,
                    draft.id,
                    MemorySkillDraftStatus.APPLYING.value,
                ),
            )
            changed = cursor.rowcount
            await cursor.close()
            row = await async_fetchone(
                conn,
                "SELECT * FROM memory_skill_drafts WHERE draft_id=?",
                (draft.id,),
            )
            if row is None:
                return None
            existing = _row_to_draft(row)
            if changed == 1:
                return existing
            if (
                existing.status == MemorySkillDraftStatus.APPLIED
                and existing.applied_skill_id == draft.applied_skill_id
                and existing.applied_ref == draft.applied_ref
            ):
                return existing
            return None

        return await self._run_async_write(
            operation_name="complete_memory_skill_draft_apply_async",
            operation=op,
        )

    async def query_drafts_async(
        self, query: MemorySkillDraftQuery
    ) -> MemorySkillDraftQueryResult:
        where_clause, params = _build_where(query)
        count_sql = f"SELECT COUNT(*) as cnt FROM memory_skill_drafts {where_clause}"
        data_sql = (
            f"SELECT * FROM memory_skill_drafts {where_clause} "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )

        async def op(conn: aiosqlite.Connection) -> MemorySkillDraftQueryResult:
            count_row = await async_fetchone(conn, count_sql, tuple(params))
            total_count = int(count_row["cnt"]) if count_row is not None else 0
            rows = await async_fetchall(
                conn,
                data_sql,
                tuple(params) + (query.limit, query.offset),
            )
            items = tuple(draft_to_summary(_row_to_draft(row)) for row in rows)
            return MemorySkillDraftQueryResult(
                items=items,
                total_count=total_count,
                offset=query.offset,
                limit=query.limit,
            )

        return await self._run_async_read(op)


def generate_memory_skill_draft_id() -> str:
    return f"msd-{uuid.uuid4().hex[:24]}"


def _draft_to_params(draft: MemorySkillDraft) -> tuple[object, ...]:
    files_json = json.dumps(
        [file.model_dump(mode="json") for file in draft.files],
        separators=(",", ":"),
    )
    validation_json = json.dumps(
        [message.model_dump(mode="json") for message in draft.validation_messages],
        separators=(",", ":"),
    )
    workspace_ids_json = json.dumps(draft.workspace_ids, separators=(",", ":"))
    source_ids_json = json.dumps(draft.source_memory_ids, separators=(",", ":"))
    return (
        draft.id,
        draft.status.value,
        draft.scope_kind.value,
        draft.workspace_id,
        workspace_ids_json,
        source_ids_json,
        draft.draft_kind.value,
        draft.runtime_name,
        draft.description,
        draft.instructions,
        files_json,
        validation_json,
        draft.generation_error,
        draft.applied_skill_id,
        draft.applied_ref,
        draft.created_at.isoformat(),
        draft.updated_at.isoformat(),
        draft.validated_at.isoformat() if draft.validated_at else None,
        draft.applied_at.isoformat() if draft.applied_at else None,
    )


def _row_to_draft(row: sqlite3.Row) -> MemorySkillDraft:
    return MemorySkillDraft(
        id=str(row["draft_id"]),
        status=MemorySkillDraftStatus(str(row["status"])),
        scope_kind=MemorySkillDraftScopeKind(str(row["scope_kind"])),
        workspace_id=_nullable_str(row["workspace_id"]),
        workspace_ids=_load_string_tuple(row["workspace_ids_json"]),
        source_memory_ids=_load_string_tuple(row["source_memory_ids_json"]),
        draft_kind=MemorySkillDraftKind(str(row["draft_kind"])),
        runtime_name=str(row["runtime_name"]),
        description=str(row["description"]),
        instructions=str(row["instructions"]),
        files=_load_files(row["files_json"]),
        validation_messages=_load_validation_messages(row["validation_messages_json"]),
        generation_error=str(row["generation_error"]),
        applied_skill_id=_nullable_str(row["applied_skill_id"]),
        applied_ref=_nullable_str(row["applied_ref"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        validated_at=_parse_dt_or_none(row["validated_at"]),
        applied_at=_parse_dt_or_none(row["applied_at"]),
    )


def _load_string_tuple(raw: object) -> tuple[str, ...]:
    loaded = json.loads(str(raw or "[]"))
    if not isinstance(loaded, list):
        return ()
    return tuple(str(item).strip() for item in loaded if str(item).strip())


def _load_files(raw: object) -> tuple[MemorySkillDraftFile, ...]:
    loaded = json.loads(str(raw or "[]"))
    if not isinstance(loaded, list):
        return ()
    return tuple(
        MemorySkillDraftFile.model_validate(item)
        for item in loaded
        if isinstance(item, dict)
    )


def _load_validation_messages(
    raw: object,
) -> tuple[MemorySkillDraftValidationMessage, ...]:
    loaded = json.loads(str(raw or "[]"))
    if not isinstance(loaded, list):
        return ()
    return tuple(
        MemorySkillDraftValidationMessage.model_validate(item)
        for item in loaded
        if isinstance(item, dict)
    )


def _build_where(query: MemorySkillDraftQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.scope_kind is not None:
        clauses.append("scope_kind = ?")
        params.append(query.scope_kind.value)
    if query.workspace_id is not None:
        clauses.append("(workspace_id = ? OR workspace_ids_json LIKE ? ESCAPE '\\')")
        params.append(query.workspace_id)
        params.append(f"%{_escape_like(json.dumps(query.workspace_id))}%")
    if query.status is not None:
        clauses.append("status = ?")
        params.append(query.status.value)
    if query.draft_kind is not None:
        clauses.append("draft_kind = ?")
        params.append(query.draft_kind.value)
    if query.text_query.strip():
        clauses.append(
            "(runtime_name LIKE ? OR description LIKE ? OR instructions LIKE ?)"
        )
        like_value = f"%{query.text_query.strip()}%"
        params.extend((like_value, like_value, like_value))
    where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _nullable_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _parse_dt(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _parse_dt_or_none(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return datetime.fromisoformat(text)
