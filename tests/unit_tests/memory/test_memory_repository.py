# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from relay_teams.memory.models import (
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository, generate_memory_id
from relay_teams.persistence.sqlite_repository import async_fetchall, async_fetchone

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**overrides: object) -> MemoryEntry:
    now = datetime.now(tz=timezone.utc)
    base: dict[str, object] = {
        "id": generate_memory_id(),
        "tier": MemoryTier.PERSISTENT,
        "scope": MemoryScope.WORKSPACE,
        "workspace_id": "ws-test",
        "kind": MemoryEntryKind.FACT,
        "content": MemoryContent(title="Test entry", body="Body content here"),
        "source": MemorySourceKind.MANUAL,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return MemoryEntry(**base)  # type: ignore[arg-type]


@pytest.fixture
def repo(tmp_path: Path) -> MemoryBankRepository:
    db_file = tmp_path / "test_memory.db"
    return MemoryBankRepository(db_file)


# ---------------------------------------------------------------------------
# AC-5: Table created on first use
# ---------------------------------------------------------------------------


class TestSchemaInit:
    async def test_table_created_on_init(self, repo: MemoryBankRepository) -> None:
        row = await repo._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='memory_entries'",
            )
        )
        assert row is not None

    async def test_indexes_created(self, repo: MemoryBankRepository) -> None:
        rows = await repo._run_async_read(
            lambda conn: async_fetchall(
                conn,
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name LIKE 'idx_memory_entries_%'",
            )
        )
        index_names = {str(row["name"]) for row in rows}
        expected = {
            "idx_memory_entries_workspace_tier",
            "idx_memory_entries_workspace_scope",
            "idx_memory_entries_session",
            "idx_memory_entries_role",
            "idx_memory_entries_run",
            "idx_memory_entries_expires",
            "idx_memory_entries_source_ref",
        }
        assert expected == index_names

    async def test_legacy_role_memories_are_migrated_and_dropped(
        self, tmp_path: Path
    ) -> None:
        db_file = tmp_path / "legacy_memory.db"
        with sqlite3.connect(db_file) as conn:
            conn.execute(
                """CREATE TABLE role_memories (
                    role_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    content_markdown TEXT NOT NULL,
                    performance_json TEXT NOT NULL DEFAULT '',
                    assessment_state_json TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                """INSERT INTO role_memories (
                    role_id,
                    workspace_id,
                    content_markdown,
                    performance_json,
                    assessment_state_json,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    "writer",
                    "ws-legacy",
                    "Prefer concise summaries.",
                    '{"total_tasks": 4}',
                    '{"needs": "examples"}',
                    "2026-03-15T08:30:00+00:00",
                ),
            )
            conn.commit()

        migrated_repo = MemoryBankRepository(db_file)
        result = await migrated_repo.query_entries_async(
            MemoryQuery(
                workspace_id="ws-legacy",
                scope=MemoryScope.ROLE,
                role_id="writer",
                limit=10,
            )
        )
        table_row = await migrated_repo._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='role_memories'",
            )
        )

        assert table_row is None
        assert result.total_count == 3
        assert {item.kind for item in result.items} == {
            MemoryEntryKind.SUMMARY,
            MemoryEntryKind.INSIGHT,
        }
        assert {item.source for item in result.items} == {
            MemorySourceKind.CONSOLIDATION
        }
        assert all(item.tier == MemoryTier.PERSISTENT for item in result.items)
        assert all(item.scope == MemoryScope.ROLE for item in result.items)
        assert all(item.confidence_score == 0.8 for item in result.items)
        assert {tag for item in result.items for tag in item.tags} >= {
            "legacy",
            "role-memory",
            "role-performance",
            "role-assessment",
        }


# ---------------------------------------------------------------------------
# AC-6: CRUD operations
# ---------------------------------------------------------------------------


class TestCRUD:
    async def test_create_and_read(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        await repo.create_entry_async(entry=entry)
        loaded = await repo.get_by_id_async(entry.id)
        assert loaded is not None
        assert loaded.id == entry.id
        assert loaded.content.title == "Test entry"
        assert loaded.tier == MemoryTier.PERSISTENT

    async def test_read_nonexistent_returns_none(
        self, repo: MemoryBankRepository
    ) -> None:
        assert await repo.get_by_id_async("mem-nonexistent") is None

    async def test_update(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        await repo.create_entry_async(entry=entry)
        updated = entry.model_copy(
            update={
                "content": MemoryContent(title="Updated", body="New body"),
                "version": 2,
            }
        )
        result = await repo.update_entry_async(entry.id, entry=updated)
        assert result.content.title == "Updated"
        assert result.version == 2

        reloaded = await repo.get_by_id_async(entry.id)
        assert reloaded is not None
        assert reloaded.content.title == "Updated"

    async def test_delete(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        await repo.create_entry_async(entry=entry)
        assert await repo.delete_entry_async(entry.id) is True
        assert await repo.get_by_id_async(entry.id) is None

    async def test_delete_nonexistent(self, repo: MemoryBankRepository) -> None:
        assert await repo.delete_entry_async("mem-nonexistent") is False


# ---------------------------------------------------------------------------
# AC-14: Structured query with filtering
# ---------------------------------------------------------------------------


class TestQuery:
    async def _seed_entries(self, repo: MemoryBankRepository) -> list[MemoryEntry]:
        entries = [
            _make_entry(
                id="mem-p1",
                tier=MemoryTier.PERSISTENT,
                scope=MemoryScope.WORKSPACE,
                kind=MemoryEntryKind.CONSTRAINT,
                tags=("python", "pydantic"),
            ),
            _make_entry(
                id="mem-w1",
                tier=MemoryTier.WORKING,
                scope=MemoryScope.SESSION,
                session_id="sess-1",
                run_id="run-1",
                kind=MemoryEntryKind.INSIGHT,
                tags=("pattern",),
            ),
            _make_entry(
                id="mem-m1",
                tier=MemoryTier.MEDIUM_TERM,
                scope=MemoryScope.ROLE,
                role_id="role-crafter",
                kind=MemoryEntryKind.DECISION,
                tags=("architecture",),
                confidence_score=0.5,
            ),
        ]
        for e in entries:
            await repo.create_entry_async(entry=e)
        return entries

    async def test_filter_by_tier(self, repo: MemoryBankRepository) -> None:
        await self._seed_entries(repo)
        result = await repo.query_entries_async(
            MemoryQuery(workspace_id="ws-test", tier=MemoryTier.PERSISTENT)
        )
        assert result.total_count >= 1
        assert all(s.tier == MemoryTier.PERSISTENT for s in result.items)

    async def test_filter_by_kind(self, repo: MemoryBankRepository) -> None:
        await self._seed_entries(repo)
        result = await repo.query_entries_async(
            MemoryQuery(workspace_id="ws-test", kind=MemoryEntryKind.INSIGHT)
        )
        assert result.total_count >= 1
        assert all(s.kind == MemoryEntryKind.INSIGHT for s in result.items)

    async def test_filter_by_min_confidence(self, repo: MemoryBankRepository) -> None:
        await self._seed_entries(repo)
        result = await repo.query_entries_async(
            MemoryQuery(workspace_id="ws-test", min_confidence=0.8)
        )
        assert all(s.confidence_score >= 0.8 for s in result.items)

    async def test_pagination(self, repo: MemoryBankRepository) -> None:
        await self._seed_entries(repo)
        result = await repo.query_entries_async(
            MemoryQuery(workspace_id="ws-test", limit=2, offset=0)
        )
        assert len(result.items) <= 2
        assert result.limit == 2
        assert result.offset == 0


# ---------------------------------------------------------------------------
# AC-12: TTL expiry
# ---------------------------------------------------------------------------


class TestExpiry:
    async def test_expire_entries(self, repo: MemoryBankRepository) -> None:
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        entry = _make_entry(
            status=MemoryEntryStatus.ACTIVE,
            expires_at=past,
        )
        await repo.create_entry_async(entry=entry)

        expired_count = await repo.expire_entries_async()
        assert expired_count >= 1

        loaded = await repo.get_by_id_async(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.EXPIRED

    async def test_no_expire_future(self, repo: MemoryBankRepository) -> None:
        future = datetime.now(tz=timezone.utc) + timedelta(hours=10)
        entry = _make_entry(
            status=MemoryEntryStatus.ACTIVE,
            expires_at=future,
        )
        await repo.create_entry_async(entry=entry)

        expired_count = await repo.expire_entries_async()
        assert expired_count == 0

        loaded = await repo.get_by_id_async(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.ACTIVE


# ---------------------------------------------------------------------------
# AC-13: Confidence decay
# ---------------------------------------------------------------------------


class TestConfidenceDecay:
    async def test_decay_and_expire(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry(
            tier=MemoryTier.MEDIUM_TERM,
            confidence_score=0.21,
        )
        await repo.create_entry_async(entry=entry)

        # With min_confidence=0.2, 0.21*0.98 = 0.2058 which is still >= 0.2
        # So we need min_confidence to be above that
        count = await repo.apply_confidence_decay_async(min_confidence=0.21)
        # After decay: 0.21 * 0.98 = 0.2058, which is < 0.21 threshold
        assert count >= 1

        loaded = await repo.get_by_id_async(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.EXPIRED

    async def test_no_decay_working_tier(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry(
            tier=MemoryTier.WORKING,
            run_id="run-1",
            confidence_score=0.5,
        )
        await repo.create_entry_async(entry=entry)

        # Working tier doesn't decay, but min_confidence check still applies
        await repo.apply_confidence_decay_async(min_confidence=0.4)

        loaded = await repo.get_by_id_async(entry.id)
        assert loaded is not None
        assert loaded.confidence_score == 0.5  # unchanged -- working tier


# ---------------------------------------------------------------------------
# generate_memory_id helper
# ---------------------------------------------------------------------------


class TestGenerateId:
    async def test_generates_mem_prefix(self) -> None:
        mid = generate_memory_id()
        assert mid.startswith("mem-")
        assert len(mid) > 4
