# -*- coding: utf-8 -*-
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(**overrides: object) -> MemoryEntry:
    from datetime import datetime, timezone

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
    def test_table_created_on_init(self, repo: MemoryBankRepository) -> None:
        row = repo._run_read(
            lambda: repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_entries'"
            ).fetchone()
        )
        assert row is not None

    def test_indexes_created(self, repo: MemoryBankRepository) -> None:
        rows = repo._run_read(
            lambda: repo._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_memory_entries_%'"
            ).fetchall()
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


# ---------------------------------------------------------------------------
# AC-6: CRUD operations
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_create_and_read(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        repo.create_entry(entry=entry)
        loaded = repo.get_by_id(entry.id)
        assert loaded is not None
        assert loaded.id == entry.id
        assert loaded.content.title == "Test entry"
        assert loaded.tier == MemoryTier.PERSISTENT

    def test_read_nonexistent_returns_none(self, repo: MemoryBankRepository) -> None:
        assert repo.get_by_id("mem-nonexistent") is None

    def test_update(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        repo.create_entry(entry=entry)
        updated = entry.model_copy(
            update={
                "content": MemoryContent(title="Updated", body="New body"),
                "version": 2,
            }
        )
        result = repo.update_entry(entry.id, entry=updated)
        assert result.content.title == "Updated"
        assert result.version == 2

        reloaded = repo.get_by_id(entry.id)
        assert reloaded is not None
        assert reloaded.content.title == "Updated"

    def test_delete(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry()
        repo.create_entry(entry=entry)
        assert repo.delete_entry(entry.id) is True
        assert repo.get_by_id(entry.id) is None

    def test_delete_nonexistent(self, repo: MemoryBankRepository) -> None:
        assert repo.delete_entry("mem-nonexistent") is False


# ---------------------------------------------------------------------------
# AC-14: Structured query with filtering
# ---------------------------------------------------------------------------


class TestQuery:
    def _seed_entries(self, repo: MemoryBankRepository) -> list[MemoryEntry]:
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
            repo.create_entry(entry=e)
        return entries

    def test_filter_by_tier(self, repo: MemoryBankRepository) -> None:
        self._seed_entries(repo)
        result = repo.query_entries(
            MemoryQuery(workspace_id="ws-test", tier=MemoryTier.PERSISTENT)
        )
        assert result.total_count >= 1
        assert all(s.tier == MemoryTier.PERSISTENT for s in result.items)

    def test_filter_by_kind(self, repo: MemoryBankRepository) -> None:
        self._seed_entries(repo)
        result = repo.query_entries(
            MemoryQuery(workspace_id="ws-test", kind=MemoryEntryKind.INSIGHT)
        )
        assert result.total_count >= 1
        assert all(s.kind == MemoryEntryKind.INSIGHT for s in result.items)

    def test_filter_by_min_confidence(self, repo: MemoryBankRepository) -> None:
        self._seed_entries(repo)
        result = repo.query_entries(
            MemoryQuery(workspace_id="ws-test", min_confidence=0.8)
        )
        assert all(s.confidence_score >= 0.8 for s in result.items)

    def test_pagination(self, repo: MemoryBankRepository) -> None:
        self._seed_entries(repo)
        result = repo.query_entries(
            MemoryQuery(workspace_id="ws-test", limit=2, offset=0)
        )
        assert len(result.items) <= 2
        assert result.limit == 2
        assert result.offset == 0


# ---------------------------------------------------------------------------
# AC-12: TTL expiry
# ---------------------------------------------------------------------------


class TestExpiry:
    def test_expire_entries(self, repo: MemoryBankRepository) -> None:
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        entry = _make_entry(
            status=MemoryEntryStatus.ACTIVE,
            expires_at=past,
        )
        repo.create_entry(entry=entry)

        expired_count = repo.expire_entries()
        assert expired_count >= 1

        loaded = repo.get_by_id(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.EXPIRED

    def test_no_expire_future(self, repo: MemoryBankRepository) -> None:
        future = datetime.now(tz=timezone.utc) + timedelta(hours=10)
        entry = _make_entry(
            status=MemoryEntryStatus.ACTIVE,
            expires_at=future,
        )
        repo.create_entry(entry=entry)

        expired_count = repo.expire_entries()
        assert expired_count == 0

        loaded = repo.get_by_id(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.ACTIVE


# ---------------------------------------------------------------------------
# AC-13: Confidence decay
# ---------------------------------------------------------------------------


class TestConfidenceDecay:
    def test_decay_and_expire(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry(
            tier=MemoryTier.MEDIUM_TERM,
            confidence_score=0.21,
        )
        repo.create_entry(entry=entry)

        # With min_confidence=0.2, 0.21*0.98 = 0.2058 which is still >= 0.2
        # So we need min_confidence to be above that
        count = repo.apply_confidence_decay(min_confidence=0.21)
        # After decay: 0.21 * 0.98 = 0.2058, which is < 0.21 threshold
        assert count >= 1

        loaded = repo.get_by_id(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.EXPIRED

    def test_no_decay_working_tier(self, repo: MemoryBankRepository) -> None:
        entry = _make_entry(
            tier=MemoryTier.WORKING,
            run_id="run-1",
            confidence_score=0.5,
        )
        repo.create_entry(entry=entry)

        # Working tier doesn't decay, but min_confidence check still applies
        repo.apply_confidence_decay(min_confidence=0.4)

        loaded = repo.get_by_id(entry.id)
        assert loaded is not None
        assert loaded.confidence_score == 0.5  # unchanged -- working tier


# ---------------------------------------------------------------------------
# generate_memory_id helper
# ---------------------------------------------------------------------------


class TestGenerateId:
    def test_generates_mem_prefix(self) -> None:
        mid = generate_memory_id()
        assert mid.startswith("mem-")
        assert len(mid) > 4
