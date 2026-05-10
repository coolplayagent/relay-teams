# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from relay_teams.logger import get_logger
from relay_teams.memory.memory_defaults import (
    MEDIUM_TERM_DECAY_FACTOR,
    MEMORY_ID_PREFIX,
    PERSISTENT_DECAY_FACTOR,
)
from relay_teams.memory.models import (
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEntrySummary,
    MemoryQuery,
    MemoryQueryResult,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
    _entry_to_summary,
)
from relay_teams.persistence.sqlite_repository import (
    SharedSqliteRepository,
    async_fetchall,
    async_fetchone,
)

LOGGER = get_logger(__name__)

_SCHEMA_STATEMENTS: list[str] = [
    """\
CREATE TABLE IF NOT EXISTS memory_entries (
    memory_id         TEXT PRIMARY KEY,
    tier              TEXT NOT NULL,
    scope             TEXT NOT NULL,
    workspace_id      TEXT NOT NULL,
    session_id        TEXT,
    run_id            TEXT,
    role_id           TEXT,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active',
    content_title     TEXT NOT NULL,
    content_body      TEXT NOT NULL,
    content_context   TEXT NOT NULL DEFAULT '',
    content_outcome   TEXT NOT NULL DEFAULT '',
    tags              TEXT NOT NULL DEFAULT '',
    confidence_score  REAL NOT NULL DEFAULT 1.0,
    source            TEXT NOT NULL,
    source_ref        TEXT NOT NULL DEFAULT '',
    superseded_by_id  TEXT,
    parent_entry_id   TEXT,
    version           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    expires_at        TEXT,
    last_accessed_at  TEXT,
    access_count      INTEGER NOT NULL DEFAULT 0,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (superseded_by_id) REFERENCES memory_entries(memory_id),
    FOREIGN KEY (parent_entry_id)  REFERENCES memory_entries(memory_id)
)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_tier
    ON memory_entries(workspace_id, tier, status, updated_at DESC)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_workspace_scope
    ON memory_entries(workspace_id, scope, status, updated_at DESC)""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_session
    ON memory_entries(session_id, tier, status, updated_at DESC)
    WHERE session_id IS NOT NULL""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_role
    ON memory_entries(workspace_id, role_id, tier, status, updated_at DESC)
    WHERE role_id IS NOT NULL""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_run
    ON memory_entries(run_id, status)
    WHERE run_id IS NOT NULL""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_expires
    ON memory_entries(expires_at)
    WHERE expires_at IS NOT NULL AND status = 'active'""",
    """\
CREATE INDEX IF NOT EXISTS idx_memory_entries_source_ref
    ON memory_entries(source_ref)""",
]


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    meta_raw = str(row["metadata_json"])
    metadata: dict[str, str] = json.loads(meta_raw) if meta_raw else {}
    tags_raw = str(row["tags"]).strip()
    tags = tuple(tags_raw.split()) if tags_raw else ()

    return MemoryEntry(
        id=str(row["memory_id"]),
        tier=MemoryTier(str(row["tier"])),
        scope=MemoryScope(str(row["scope"])),
        workspace_id=str(row["workspace_id"]),
        session_id=_nullable_str(row["session_id"]),
        run_id=_nullable_str(row["run_id"]),
        role_id=_nullable_str(row["role_id"]),
        kind=MemoryEntryKind(str(row["kind"])),
        status=MemoryEntryStatus(str(row["status"])),
        content=MemoryContent(
            title=str(row["content_title"]),
            body=str(row["content_body"]),
            context=str(row["content_context"]),
            outcome=str(row["content_outcome"]),
        ),
        tags=tags,
        confidence_score=float(row["confidence_score"]),
        source=MemorySourceKind(str(row["source"])),
        source_ref=str(row["source_ref"]),
        superseded_by_id=_nullable_str(row["superseded_by_id"]),
        parent_entry_id=_nullable_str(row["parent_entry_id"]),
        version=int(row["version"]),
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        expires_at=_parse_dt_or_none(row["expires_at"]),
        last_accessed_at=_parse_dt_or_none(row["last_accessed_at"]),
        access_count=int(row["access_count"]),
        metadata=metadata,
    )


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


def _row_to_summary(row: sqlite3.Row) -> MemoryEntrySummary:
    entry = _row_to_entry(row)
    return _entry_to_summary(entry)


class MemoryBankRepository(SharedSqliteRepository):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._run_write(
            operation_name="init_memory_tables",
            operation=self._create_schema,
        )

    def _create_schema(self) -> None:
        for stmt in _SCHEMA_STATEMENTS:
            self._conn.execute(stmt)
        self._migrate_legacy_role_memories()
        self._conn.execute("DROP TABLE IF EXISTS role_daily_memories")

    def _migrate_legacy_role_memories(self) -> None:
        table = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='role_memories'"
        ).fetchone()
        if table is None:
            return

        columns = {
            str(row["name"])
            for row in self._conn.execute("PRAGMA table_info(role_memories)").fetchall()
        }
        required = {"role_id", "workspace_id", "content_markdown", "updated_at"}
        if not required.issubset(columns):
            LOGGER.warning(
                "Dropping unsupported legacy role_memories table during Memory Bank migration"
            )
            self._conn.execute("DROP TABLE role_memories")
            return

        rows = self._conn.execute("SELECT * FROM role_memories").fetchall()
        migrated_count = 0
        for row in rows:
            role_id = str(row["role_id"]).strip()
            workspace_id = str(row["workspace_id"]).strip()
            if not role_id or not workspace_id:
                continue
            updated_at = _parse_dt_or_default(row["updated_at"])
            content_markdown = str(row["content_markdown"] or "").strip()
            if content_markdown:
                source_ref = _legacy_source_ref(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    kind="summary",
                )
                if not self._legacy_memory_exists(source_ref):
                    self._insert_legacy_memory_entry(
                        role_id=role_id,
                        workspace_id=workspace_id,
                        kind=MemoryEntryKind.SUMMARY,
                        source_ref=source_ref,
                        title=f"Legacy role memory for {role_id}",
                        body=content_markdown,
                        context="Migrated from legacy role_memories.content_markdown.",
                        tags=("legacy", "role-memory"),
                        updated_at=updated_at,
                    )
                    migrated_count += 1
            performance_json = (
                str(row["performance_json"] or "").strip()
                if "performance_json" in columns
                else ""
            )
            if performance_json:
                source_ref = _legacy_source_ref(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    kind="performance",
                )
                if not self._legacy_memory_exists(source_ref):
                    self._insert_legacy_memory_entry(
                        role_id=role_id,
                        workspace_id=workspace_id,
                        kind=MemoryEntryKind.INSIGHT,
                        source_ref=source_ref,
                        title=f"Legacy role performance for {role_id}",
                        body=performance_json,
                        context="Migrated from legacy role_memories.performance_json.",
                        tags=("legacy", "role-performance"),
                        updated_at=updated_at,
                    )
                    migrated_count += 1
            assessment_json = (
                str(row["assessment_state_json"] or "").strip()
                if "assessment_state_json" in columns
                else ""
            )
            if assessment_json:
                source_ref = _legacy_source_ref(
                    role_id=role_id,
                    workspace_id=workspace_id,
                    kind="assessment",
                )
                if not self._legacy_memory_exists(source_ref):
                    self._insert_legacy_memory_entry(
                        role_id=role_id,
                        workspace_id=workspace_id,
                        kind=MemoryEntryKind.INSIGHT,
                        source_ref=source_ref,
                        title=f"Legacy role assessment for {role_id}",
                        body=assessment_json,
                        context="Migrated from legacy role_memories.assessment_state_json.",
                        tags=("legacy", "role-assessment"),
                        updated_at=updated_at,
                    )
                    migrated_count += 1

        self._conn.execute("DROP TABLE role_memories")
        if migrated_count:
            LOGGER.info(
                "Migrated %d legacy role_memories records into Memory Bank",
                migrated_count,
            )

    def _legacy_memory_exists(self, source_ref: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM memory_entries WHERE source_ref=? LIMIT 1",
            (source_ref,),
        ).fetchone()
        return row is not None

    def _insert_legacy_memory_entry(
        self,
        *,
        role_id: str,
        workspace_id: str,
        kind: MemoryEntryKind,
        source_ref: str,
        title: str,
        body: str,
        context: str,
        tags: tuple[str, ...],
        updated_at: datetime,
    ) -> None:
        entry = MemoryEntry(
            id=generate_memory_id(),
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.ROLE,
            workspace_id=workspace_id,
            role_id=role_id,
            kind=kind,
            status=MemoryEntryStatus.ACTIVE,
            content=MemoryContent(
                title=title,
                body=body,
                context=context,
                outcome="migrated",
            ),
            tags=tags,
            confidence_score=0.8,
            source=MemorySourceKind.CONSOLIDATION,
            source_ref=source_ref,
            created_at=updated_at,
            updated_at=updated_at,
            metadata={
                "imported_from": "role_memories",
                "legacy_role_id": role_id,
                "legacy_workspace_id": workspace_id,
            },
        )
        self._conn.execute(
            """INSERT INTO memory_entries(
                memory_id, tier, scope, workspace_id, session_id, run_id, role_id,
                kind, status, content_title, content_body, content_context, content_outcome,
                tags, confidence_score, source, source_ref,
                superseded_by_id, parent_entry_id, version,
                created_at, updated_at, expires_at, last_accessed_at, access_count,
                metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            self._entry_to_params(entry),
        )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_entry_async(self, *, entry: MemoryEntry) -> MemoryEntry:
        async def op(conn: aiosqlite.Connection) -> None:
            await self._async_insert_entry(conn, entry)

        await self._run_async_write(
            operation_name="create_memory_entry_async",
            operation=op,
        )
        return entry

    async def _async_insert_entry(
        self, conn: aiosqlite.Connection, entry: MemoryEntry
    ) -> None:
        cursor = await conn.execute(
            """INSERT INTO memory_entries(
                memory_id, tier, scope, workspace_id, session_id, run_id, role_id,
                kind, status, content_title, content_body, content_context, content_outcome,
                tags, confidence_score, source, source_ref,
                superseded_by_id, parent_entry_id, version,
                created_at, updated_at, expires_at, last_accessed_at, access_count,
                metadata_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            self._entry_to_params(entry),
        )
        await cursor.close()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id_async(self, memory_id: str) -> MemoryEntry | None:
        async def op(conn: aiosqlite.Connection) -> MemoryEntry | None:
            row = await async_fetchone(
                conn,
                "SELECT * FROM memory_entries WHERE memory_id=?",
                (memory_id,),
            )
            if row is None:
                return None
            return _row_to_entry(row)

        return await self._run_async_read(op)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_entry_async(
        self, memory_id: str, *, entry: MemoryEntry
    ) -> MemoryEntry:
        async def op(conn: aiosqlite.Connection) -> None:
            await self._async_do_update_entry(conn, memory_id, entry)

        await self._run_async_write(
            operation_name="update_memory_entry_async",
            operation=op,
        )
        return entry

    async def _async_do_update_entry(
        self,
        conn: aiosqlite.Connection,
        memory_id: str,
        entry: MemoryEntry,
    ) -> None:
        cursor = await conn.execute(
            """UPDATE memory_entries SET
                tier=?, scope=?, workspace_id=?, session_id=?, run_id=?, role_id=?,
                kind=?, status=?, content_title=?, content_body=?, content_context=?, content_outcome=?,
                tags=?, confidence_score=?, source=?, source_ref=?,
                superseded_by_id=?, parent_entry_id=?, version=?,
                created_at=?, updated_at=?, expires_at=?, last_accessed_at=?, access_count=?,
                metadata_json=?
            WHERE memory_id=?""",
            (*self._entry_to_params(entry)[1:], memory_id),
        )
        await cursor.close()

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_entry_async(self, memory_id: str) -> bool:
        async def op(conn: aiosqlite.Connection) -> bool:
            cursor = await conn.execute(
                "DELETE FROM memory_entries WHERE memory_id=?",
                (memory_id,),
            )
            affected = cursor.rowcount
            await cursor.close()
            return affected > 0

        return await self._run_async_write(
            operation_name="delete_memory_entry_async",
            operation=op,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def query_entries_async(self, query: MemoryQuery) -> MemoryQueryResult:
        where_clause, params = self._build_where(query)
        count_sql = f"SELECT COUNT(*) as cnt FROM memory_entries {where_clause}"
        data_sql = (
            f"SELECT * FROM memory_entries {where_clause} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        )

        async def op(conn: aiosqlite.Connection) -> MemoryQueryResult:
            count_row = await async_fetchone(conn, count_sql, tuple(params))
            total_count = int(count_row["cnt"]) if count_row is not None else 0

            rows = await async_fetchall(
                conn, data_sql, tuple(params) + (query.limit, query.offset)
            )
            items = tuple(_row_to_summary(row) for row in rows)
            return MemoryQueryResult(
                items=items,
                total_count=total_count,
                offset=query.offset,
                limit=query.limit,
            )

        return await self._run_async_read(op)

    @staticmethod
    def _build_where(query: MemoryQuery) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []

        if query.workspace_id is not None:
            clauses.append("workspace_id = ?")
            params.append(query.workspace_id)

        if query.tier is not None:
            clauses.append("tier = ?")
            params.append(query.tier.value)
        if query.scope is not None:
            clauses.append("scope = ?")
            params.append(query.scope.value)
        if query.session_id is not None:
            clauses.append("session_id = ?")
            params.append(query.session_id)
        if query.role_id is not None:
            clauses.append("role_id = ?")
            params.append(query.role_id)
        if query.kind is not None:
            clauses.append("kind = ?")
            params.append(query.kind.value)
        if query.status is not None:
            clauses.append("status = ?")
            params.append(query.status.value)
        if query.min_confidence > 0.0:
            clauses.append("confidence_score >= ?")
            params.append(query.min_confidence)
        if query.created_after is not None:
            clauses.append("created_at >= ?")
            params.append(query.created_after.isoformat())
        if query.created_before is not None:
            clauses.append("created_at <= ?")
            params.append(query.created_before.isoformat())
        if query.tags:
            for tag in query.tags:
                clauses.append("tags LIKE ?")
                params.append(f"%{tag}%")

        where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
        return where_sql, params

    # ------------------------------------------------------------------
    # Expiry sweep
    # ------------------------------------------------------------------

    async def expire_entries_async(self, now: datetime | None = None) -> int:
        now_iso = (now or datetime.now(tz=timezone.utc)).isoformat()

        async def op(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute(
                "UPDATE memory_entries SET status='expired', updated_at=? "
                "WHERE status='active' AND expires_at IS NOT NULL AND expires_at < ?",
                (now_iso, now_iso),
            )
            affected = cursor.rowcount
            await cursor.close()
            return affected

        return await self._run_async_write(
            operation_name="expire_memory_entries_async",
            operation=op,
        )

    # ------------------------------------------------------------------
    # Confidence decay
    # ------------------------------------------------------------------

    async def apply_confidence_decay_async(
        self, *, min_confidence: float = 0.2, now: datetime | None = None
    ) -> int:
        now = now or datetime.now(tz=timezone.utc)
        now_iso = now.isoformat()

        async def op(conn: aiosqlite.Connection) -> int:
            cursor = await conn.execute(
                "UPDATE memory_entries SET confidence_score = confidence_score * ?, updated_at=? "
                "WHERE tier='medium_term' AND status='active'",
                (MEDIUM_TERM_DECAY_FACTOR, now_iso),
            )
            await cursor.close()
            cursor = await conn.execute(
                "UPDATE memory_entries SET confidence_score = confidence_score * ?, updated_at=? "
                "WHERE tier='persistent' AND status='active'",
                (PERSISTENT_DECAY_FACTOR, now_iso),
            )
            await cursor.close()
            cursor = await conn.execute(
                "UPDATE memory_entries SET status='expired', updated_at=? "
                "WHERE status='active' AND confidence_score < ?",
                (now_iso, min_confidence),
            )
            affected = cursor.rowcount
            await cursor.close()
            return affected

        return await self._run_async_write(
            operation_name="apply_confidence_decay_async",
            operation=op,
        )

    # ------------------------------------------------------------------
    # Capacity helpers
    # ------------------------------------------------------------------

    async def count_entries_async(
        self,
        *,
        workspace_id: str,
        tier: MemoryTier | None = None,
        run_id: str | None = None,
        status: MemoryEntryStatus | None = None,
    ) -> int:
        clauses: list[str] = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier.value)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where_sql = "WHERE " + " AND ".join(clauses)

        async def op(conn: aiosqlite.Connection) -> int:
            row = await async_fetchone(
                conn,
                f"SELECT COUNT(*) as cnt FROM memory_entries {where_sql}",
                tuple(params),
            )
            return int(row["cnt"]) if row is not None else 0

        return await self._run_async_read(op)

    async def expire_oldest_async(
        self,
        *,
        workspace_id: str,
        tier: MemoryTier | None = None,
        run_id: str | None = None,
        status: MemoryEntryStatus = MemoryEntryStatus.ACTIVE,
        count: int = 1,
    ) -> int:
        """Expire the oldest *count* entries matching the given filters."""
        clauses: list[str] = ["workspace_id = ?"]
        params: list[object] = [workspace_id]
        if tier is not None:
            clauses.append("tier = ?")
            params.append(tier.value)
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        clauses.append("status = ?")
        params.append(status.value)
        where_sql = "WHERE " + " AND ".join(clauses)

        now_iso = datetime.now(tz=timezone.utc).isoformat()

        async def op(conn: aiosqlite.Connection) -> int:
            ids = await async_fetchall(
                conn,
                f"SELECT memory_id FROM memory_entries {where_sql} "
                f"ORDER BY created_at ASC LIMIT ?",
                tuple(params) + (count,),
            )
            affected = 0
            for row in ids:
                cursor = await conn.execute(
                    "UPDATE memory_entries SET status='expired', updated_at=? "
                    "WHERE memory_id=?",
                    (now_iso, str(row["memory_id"])),
                )
                affected += cursor.rowcount
                await cursor.close()
            return affected

        return await self._run_async_write(
            operation_name="expire_oldest_memory_entries_async",
            operation=op,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _entry_to_params(entry: MemoryEntry) -> tuple[object, ...]:
        tags_str = " ".join(entry.tags)
        meta_json = json.dumps(entry.metadata, separators=(",", ":"))
        return (
            entry.id,
            entry.tier.value,
            entry.scope.value,
            entry.workspace_id,
            entry.session_id,
            entry.run_id,
            entry.role_id,
            entry.kind.value,
            entry.status.value,
            entry.content.title,
            entry.content.body,
            entry.content.context,
            entry.content.outcome,
            tags_str,
            entry.confidence_score,
            entry.source.value,
            entry.source_ref,
            entry.superseded_by_id,
            entry.parent_entry_id,
            entry.version,
            entry.created_at.isoformat(),
            entry.updated_at.isoformat(),
            entry.expires_at.isoformat() if entry.expires_at else None,
            entry.last_accessed_at.isoformat() if entry.last_accessed_at else None,
            entry.access_count,
            meta_json,
        )


def generate_memory_id() -> str:
    return f"{MEMORY_ID_PREFIX}{uuid.uuid4().hex[:24]}"


def _parse_dt_or_default(value: object) -> datetime:
    try:
        if value is not None and str(value).strip():
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
    except ValueError:
        pass
    return datetime.now(tz=timezone.utc)


def _legacy_source_ref(*, role_id: str, workspace_id: str, kind: str) -> str:
    return f"legacy-role-memories:{workspace_id}:{role_id}:{kind}"
