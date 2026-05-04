# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryConsolidationRequest,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemorySearchRequest,
    MemorySourceKind,
    MemoryTier,
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService


@pytest.fixture
def service(tmp_path: Path) -> MemoryBankService:
    db_file = tmp_path / "test_async.db"
    repo = MemoryBankRepository(db_file)
    return MemoryBankService(repository=repo)


def _create_request(**overrides: object) -> CreateMemoryEntryRequest:
    base: dict[str, object] = {
        "tier": MemoryTier.WORKING,
        "scope": MemoryScope.SESSION,
        "workspace_id": "ws-async",
        "session_id": "sess-1",
        "run_id": "run-1",
        "kind": MemoryEntryKind.INSIGHT,
        "content": MemoryContent(title="Async Test", body="Testing async paths"),
        "source": MemorySourceKind.TASK_RESULT,
    }
    base.update(overrides)
    return CreateMemoryEntryRequest(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Async create / get / update / delete / list
# ---------------------------------------------------------------------------


class TestAsyncCreateEntry:
    def test_create_async_persistent(self, service: MemoryBankService) -> None:
        req = _create_request(
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
            session_id=None,
            run_id=None,
        )
        entry = asyncio.run(service.create_entry_async(req))
        assert entry.id.startswith("mem-")
        assert entry.tier == MemoryTier.PERSISTENT

    def test_create_async_with_ttl(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = asyncio.run(service.create_entry_async(req))
        assert entry.expires_at is not None

    def test_create_async_with_confidence(self, service: MemoryBankService) -> None:
        req = _create_request(confidence_score=0.5, tags=("a", "b"))
        entry = asyncio.run(service.create_entry_async(req))
        assert entry.confidence_score == 0.5
        assert entry.tags == ("a", "b")


class TestAsyncGetEntry:
    def test_get_async_existing(self, service: MemoryBankService) -> None:
        req = _create_request()
        created = service.create_entry(req)
        result = asyncio.run(service.get_entry_async(created.id))
        assert result is not None
        assert result.id == created.id

    def test_get_async_missing(self, service: MemoryBankService) -> None:
        result = asyncio.run(service.get_entry_async("mem-nonexistent"))
        assert result is None


class TestAsyncListEntries:
    def test_list_async_returns_entries(self, service: MemoryBankService) -> None:
        service.create_entry(_create_request())
        service.create_entry(_create_request(run_id="run-2"))
        query = MemoryQuery(workspace_id="ws-async")
        result = asyncio.run(service.list_entries_async(query))
        assert result.total_count == 2


class TestAsyncUpdateEntry:
    def test_update_async_existing(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        update = UpdateMemoryEntryRequest(
            content=MemoryContent(title="Updated", body="New body"),
        )
        result = asyncio.run(service.update_entry_async(created.id, update))
        assert result is not None
        assert result.content.title == "Updated"

    def test_update_async_missing(self, service: MemoryBankService) -> None:
        update = UpdateMemoryEntryRequest(
            content=MemoryContent(title="X", body="Y"),
        )
        result = asyncio.run(service.update_entry_async("mem-nope", update))
        assert result is None

    def test_update_async_various_fields(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        update = UpdateMemoryEntryRequest(
            tags=("new-tag",),
            confidence_score=0.3,
            status=MemoryEntryStatus.EXPIRED,
        )
        result = asyncio.run(service.update_entry_async(created.id, update))
        assert result is not None
        assert result.tags == ("new-tag",)
        assert result.confidence_score == 0.3


class TestAsyncDeleteEntry:
    def test_delete_async_existing(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        result = asyncio.run(service.delete_entry_async(created.id))
        assert result is True

    def test_delete_async_missing(self, service: MemoryBankService) -> None:
        result = asyncio.run(service.delete_entry_async("mem-nope"))
        assert result is False


# ---------------------------------------------------------------------------
# Async consolidation
# ---------------------------------------------------------------------------


class TestAsyncConsolidation:
    def test_consolidate_async(self, service: MemoryBankService) -> None:
        req = _create_request(
            tier=MemoryTier.WORKING,
            confidence_score=0.95,
        )
        service.create_entry(req)

        consolidation = MemoryConsolidationRequest(
            workspace_id="ws-async",
            session_id="sess-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
        )
        result = asyncio.run(service.consolidate_async(consolidation))
        assert result.consolidated_entry_count == 1

    def test_consolidate_async_with_filters(self, service: MemoryBankService) -> None:
        service.create_entry(
            _create_request(kind=MemoryEntryKind.INSIGHT, confidence_score=0.95)
        )
        service.create_entry(
            _create_request(
                kind=MemoryEntryKind.CONSTRAINT, confidence_score=0.95, run_id="r2"
            )
        )
        consolidation = MemoryConsolidationRequest(
            workspace_id="ws-async",
            session_id="sess-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            filter_kind=MemoryEntryKind.INSIGHT,
        )
        result = asyncio.run(service.consolidate_async(consolidation))
        assert result.consolidated_entry_count == 1

    def test_consolidate_async_persistent(self, service: MemoryBankService) -> None:
        req = _create_request(
            tier=MemoryTier.MEDIUM_TERM,
            scope=MemoryScope.SESSION,
            confidence_score=0.95,
        )
        service.create_entry(req)
        consolidation = MemoryConsolidationRequest(
            workspace_id="ws-async",
            session_id="sess-1",
            target_tier=MemoryTier.PERSISTENT,
            target_scope=MemoryScope.WORKSPACE,
        )
        result = asyncio.run(service.consolidate_async(consolidation))
        assert result.consolidated_entry_count == 1


# ---------------------------------------------------------------------------
# Async forget_expired
# ---------------------------------------------------------------------------


class TestAsyncForgetExpired:
    def test_forget_async(self, service: MemoryBankService) -> None:
        result = asyncio.run(service.forget_expired_async())
        assert result == 0

    def test_forget_async_with_expired_entries(
        self, service: MemoryBankService
    ) -> None:
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        req = _create_request(expires_at=past)
        service.create_entry(req)
        count = asyncio.run(service.forget_expired_async())
        assert count >= 1


# ---------------------------------------------------------------------------
# Async search
# ---------------------------------------------------------------------------


class TestAsyncSearch:
    def test_search_async_fallback(self, service: MemoryBankService) -> None:
        service.create_entry(
            _create_request(
                content=MemoryContent(title="Python tip", body="Use dataclasses"),
            )
        )
        request = MemorySearchRequest(
            workspace_id="ws-async",
            text_query="python",
        )
        result = asyncio.run(service.search_async(request))
        assert result.total_count == 1

    def test_search_async_no_match(self, service: MemoryBankService) -> None:
        service.create_entry(_create_request())
        request = MemorySearchRequest(
            workspace_id="ws-async",
            text_query="nonexistent_query_xyz",
        )
        result = asyncio.run(service.search_async(request))
        assert result.total_count == 0


# ---------------------------------------------------------------------------
# Service update edge cases
# ---------------------------------------------------------------------------


class TestUpdateEdgeCases:
    def test_update_missing_entry(self, service: MemoryBankService) -> None:
        update = UpdateMemoryEntryRequest(
            content=MemoryContent(title="X", body="Y"),
        )
        result = service.update_entry("mem-nonexistent", update)
        assert result is None

    def test_update_confidence_below_threshold_auto_expires(
        self, service: MemoryBankService
    ) -> None:
        created = service.create_entry(_create_request())
        update = UpdateMemoryEntryRequest(confidence_score=0.01)
        result = service.update_entry(created.id, update)
        assert result is not None
        assert result.status == MemoryEntryStatus.EXPIRED

    def test_update_with_metadata(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        update = UpdateMemoryEntryRequest(metadata={"key": "value"})
        result = service.update_entry(created.id, update)
        assert result is not None
        assert result.metadata == {"key": "value"}

    def test_update_expires_at(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        new_expires = datetime.now(tz=timezone.utc) + timedelta(days=30)
        update = UpdateMemoryEntryRequest(expires_at=new_expires)
        result = service.update_entry(created.id, update)
        assert result is not None
        assert result.expires_at is not None

    def test_update_status(self, service: MemoryBankService) -> None:
        created = service.create_entry(_create_request())
        update = UpdateMemoryEntryRequest(status=MemoryEntryStatus.EXPIRED)
        result = service.update_entry(created.id, update)
        assert result is not None
        assert result.status == MemoryEntryStatus.EXPIRED


# ---------------------------------------------------------------------------
# Search fallback edge cases
# ---------------------------------------------------------------------------


class TestSearchFallback:
    def test_search_fallback_with_tier_filter(self, service: MemoryBankService) -> None:
        service.create_entry(
            _create_request(
                tier=MemoryTier.WORKING,
                content=MemoryContent(title="pattern X", body="body content"),
            )
        )
        request = MemorySearchRequest(
            workspace_id="ws-async",
            text_query="pattern",
            tier=MemoryTier.WORKING,
        )
        result = service.search(request)
        assert result.total_count == 1

    def test_search_fallback_no_results(self, service: MemoryBankService) -> None:
        request = MemorySearchRequest(
            workspace_id="ws-nonexistent",
            text_query="anything",
        )
        result = service.search(request)
        assert result.total_count == 0


# ---------------------------------------------------------------------------
# FTS5 indexing with mock retrieval service
# ---------------------------------------------------------------------------


class TestFTSIndexing:
    def test_index_entry_with_retrieval_service(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test_fts.db")
        mock_retrieval = MagicMock()
        service = MemoryBankService(repository=repo, retrieval_service=mock_retrieval)
        service.create_entry(_create_request())
        mock_retrieval.upsert_documents.assert_called_once()

    def test_index_entry_skips_non_active(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test_fts_skip.db")
        mock_retrieval = MagicMock()
        service = MemoryBankService(repository=repo, retrieval_service=mock_retrieval)
        created = service.create_entry(_create_request())
        # Update status to expired
        service.update_entry(
            created.id, UpdateMemoryEntryRequest(status=MemoryEntryStatus.EXPIRED)
        )
        # upsert_documents called only once (initial create)
        assert mock_retrieval.upsert_documents.call_count == 1

    def test_index_entry_handles_retrieval_failure(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test_fts_fail.db")
        mock_retrieval = MagicMock()
        mock_retrieval.upsert_documents.side_effect = RuntimeError("FTS error")
        service = MemoryBankService(repository=repo, retrieval_service=mock_retrieval)
        # Should not raise
        entry = service.create_entry(_create_request())
        assert entry is not None

    def test_search_fts_with_hits(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test_fts_search.db")
        mock_retrieval = MagicMock()
        hit = MagicMock()
        hit.document_id = "mem-dummy"
        hit.score = 0.95
        hit.rank = 1
        hit.snippet = "found text"
        mock_retrieval.search.return_value = [hit]

        service = MemoryBankService(repository=repo, retrieval_service=mock_retrieval)
        # Create an entry with matching id
        created = service.create_entry(_create_request())

        # Change the hit to match the real entry id
        hit.document_id = created.id

        request = MemorySearchRequest(
            workspace_id="ws-async",
            text_query="test query",
        )
        result = service.search(request)
        assert result.total_count >= 1

    def test_search_fts_no_hits(self, tmp_path: Path) -> None:
        repo = MemoryBankRepository(tmp_path / "test_fts_no_hits.db")
        mock_retrieval = MagicMock()
        mock_retrieval.search.return_value = []
        service = MemoryBankService(repository=repo, retrieval_service=mock_retrieval)
        request = MemorySearchRequest(
            workspace_id="ws-async",
            text_query="no match",
        )
        result = service.search(request)
        assert result.total_count == 0


# ---------------------------------------------------------------------------
# _source_tier_for
# ---------------------------------------------------------------------------


class TestSourceTierFor:
    def test_persistent_sources_medium_term(self) -> None:
        assert (
            MemoryBankService._source_tier_for(MemoryTier.PERSISTENT)
            == MemoryTier.MEDIUM_TERM
        )

    def test_medium_term_sources_working(self) -> None:
        assert (
            MemoryBankService._source_tier_for(MemoryTier.MEDIUM_TERM)
            == MemoryTier.WORKING
        )

    def test_working_sources_working(self) -> None:
        assert (
            MemoryBankService._source_tier_for(MemoryTier.WORKING) == MemoryTier.WORKING
        )


# ---------------------------------------------------------------------------
# Condensation placeholder
# ---------------------------------------------------------------------------


class TestCondensation:
    def test_condense_raises_not_implemented(self, service: MemoryBankService) -> None:
        with pytest.raises(NotImplementedError, match="FE-2"):
            service.condense("ws-async")
