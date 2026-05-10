# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    GlobalMemorySearchRequest,
    MemoryContent,
    MemoryConsolidationRequest,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryScope,
    MemorySearchRequest,
    MemorySearchResult,
    MemorySourceKind,
    MemoryTier,
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path: Path) -> MemoryBankService:
    db_file = tmp_path / "test_memory.db"
    repo = MemoryBankRepository(db_file)
    return MemoryBankService(repository=repo)


def _create_request(**overrides: object) -> CreateMemoryEntryRequest:
    base: dict[str, object] = {
        "tier": MemoryTier.WORKING,
        "scope": MemoryScope.SESSION,
        "workspace_id": "ws-test",
        "session_id": "sess-1",
        "run_id": "run-1",
        "kind": MemoryEntryKind.INSIGHT,
        "content": MemoryContent(title="Discovery", body="Found a useful pattern"),
        "source": MemorySourceKind.TASK_RESULT,
    }
    base.update(overrides)
    return CreateMemoryEntryRequest(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AC-14: Service create
# ---------------------------------------------------------------------------


class TestCreateEntry:
    async def test_create_persistent(self, service: MemoryBankService) -> None:
        req = _create_request(
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
        )
        entry = await service.create_entry_async(req)
        assert entry.id.startswith("mem-")
        assert entry.tier == MemoryTier.PERSISTENT
        assert entry.confidence_score == 1.0
        assert entry.expires_at is None  # persistent has no TTL

    async def test_create_working_has_ttl(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)
        assert entry.expires_at is not None
        assert entry.expires_at > datetime.now(tz=timezone.utc)

    async def test_create_with_tags(self, service: MemoryBankService) -> None:
        req = _create_request(tags=("python", "pydantic"))
        entry = await service.create_entry_async(req)
        assert entry.tags == ("python", "pydantic")


# ---------------------------------------------------------------------------
# AC-10: Updating
# ---------------------------------------------------------------------------


class TestUpdateEntry:
    async def test_update_increments_version(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)
        original_updated_at = entry.updated_at

        update = UpdateMemoryEntryRequest(
            content=MemoryContent(title="Updated title", body="Updated body")
        )
        updated = await service.update_entry_async(entry.id, update)
        assert updated is not None
        assert updated.version == 2
        assert updated.content.title == "Updated title"
        assert updated.updated_at >= original_updated_at

    async def test_update_nonexistent_returns_none(
        self, service: MemoryBankService
    ) -> None:
        update = UpdateMemoryEntryRequest(content=MemoryContent(title="X", body="Y"))
        assert await service.update_entry_async("mem-nonexistent", update) is None

    async def test_update_auto_expires_low_confidence(
        self, service: MemoryBankService
    ) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)

        update = UpdateMemoryEntryRequest(confidence_score=0.1)
        updated = await service.update_entry_async(entry.id, update)
        assert updated is not None
        assert updated.status == MemoryEntryStatus.EXPIRED


# ---------------------------------------------------------------------------
# AC-9: Consolidation
# ---------------------------------------------------------------------------


class TestConsolidation:
    async def test_consolidate_working_to_medium_term(
        self, service: MemoryBankService
    ) -> None:
        req = _create_request()
        await service.create_entry_async(req)

        result = await service.consolidate_async(
            MemoryConsolidationRequest(
                workspace_id="ws-test",
                session_id="sess-1",
                target_tier=MemoryTier.MEDIUM_TERM,
                target_scope=MemoryScope.SESSION,
            )
        )
        assert result.source_entry_count >= 1
        assert result.consolidated_entry_count >= 1
        assert len(result.new_entry_ids) >= 1
        assert len(result.superseded_entry_ids) >= 1

    async def test_consolidate_target_cannot_be_working(
        self, service: MemoryBankService
    ) -> None:
        with pytest.raises(Exception):
            await service.consolidate_async(
                MemoryConsolidationRequest(
                    workspace_id="ws-test",
                    target_tier=MemoryTier.WORKING,
                    target_scope=MemoryScope.WORKSPACE,
                )
            )


# ---------------------------------------------------------------------------
# Forgetting
# ---------------------------------------------------------------------------


class TestForgetting:
    async def test_forget_expired(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)

        # Manually set expires_at to past
        past = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        update = UpdateMemoryEntryRequest(expires_at=past)
        await service.update_entry_async(entry.id, update)

        count = await service.forget_expired_async()
        assert count >= 1

        loaded = await service.get_entry_async(entry.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.EXPIRED


# ---------------------------------------------------------------------------
# Search (stub)
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_finds_match(self, service: MemoryBankService) -> None:
        req = _create_request(
            content=MemoryContent(
                title="Pydantic validation", body="Uses Pydantic v2 models"
            )
        )
        await service.create_entry_async(req)
        result = await service.search_async(
            MemorySearchRequest(
                workspace_id="ws-test",
                text_query="pydantic",
            )
        )
        assert result.total_count >= 1
        assert "pydantic" in result.items[0].entry.content_title.lower()

    async def test_search_no_match(self, service: MemoryBankService) -> None:
        req = _create_request()
        await service.create_entry_async(req)
        result = await service.search_async(
            MemorySearchRequest(
                workspace_id="ws-test",
                text_query="xyznonexistent",
            )
        )
        assert result.total_count == 0

    async def test_global_search_finds_entries_across_workspaces(
        self, service: MemoryBankService
    ) -> None:
        await service.create_entry_async(
            _create_request(
                workspace_id="ws-alpha",
                content=MemoryContent(
                    title="Shared constraint",
                    body="Prefer explicit Pydantic models for contracts",
                ),
            )
        )
        await service.create_entry_async(
            _create_request(
                workspace_id="ws-beta",
                content=MemoryContent(
                    title="Runtime note",
                    body="Pydantic validation should stay strict",
                ),
            )
        )

        result = await service.search_global_async(
            GlobalMemorySearchRequest(text_query="pydantic", limit=10)
        )

        assert result.total_count == 2
        assert {hit.entry.workspace_id for hit in result.items} == {
            "ws-alpha",
            "ws-beta",
        }

    async def test_global_search_delegates_when_workspace_is_supplied(
        self, service: MemoryBankService
    ) -> None:
        await service.create_entry_async(
            _create_request(
                workspace_id="ws-alpha",
                content=MemoryContent(title="Alpha note", body="Alpha Pydantic rule"),
            )
        )
        await service.create_entry_async(
            _create_request(
                workspace_id="ws-beta",
                content=MemoryContent(title="Beta note", body="Beta Pydantic rule"),
            )
        )

        result = await service.search_global_async(
            GlobalMemorySearchRequest(
                workspace_id="ws-alpha",
                text_query="pydantic",
                limit=10,
            )
        )

        assert result.total_count == 1
        assert result.items[0].entry.workspace_id == "ws-alpha"


# ---------------------------------------------------------------------------
# Get / List / Delete
# ---------------------------------------------------------------------------


class TestGetListDelete:
    async def test_get_existing(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)
        loaded = await service.get_entry_async(entry.id)
        assert loaded is not None
        assert loaded.id == entry.id

    async def test_get_nonexistent(self, service: MemoryBankService) -> None:
        assert await service.get_entry_async("mem-none") is None

    async def test_list_entries(self, service: MemoryBankService) -> None:
        await service.create_entry_async(_create_request())
        result = await service.list_entries_async(MemoryQuery(workspace_id="ws-test"))
        assert result.total_count >= 1

    async def test_delete_entry(self, service: MemoryBankService) -> None:
        req = _create_request()
        entry = await service.create_entry_async(req)
        assert await service.delete_entry_async(entry.id) is True
        assert await service.get_entry_async(entry.id) is None


# ---------------------------------------------------------------------------
# FTS5 search
# ---------------------------------------------------------------------------


class TestSearchFTS5:
    """Tests for the search method covering both FTS5-backed and fallback paths."""

    async def test_search_method_exists(self, service: MemoryBankService) -> None:
        """The search method must be callable and return MemorySearchResult."""
        req = _create_request()
        await service.create_entry_async(req)
        result = await service.search_async(
            MemorySearchRequest(workspace_id="ws-test", text_query="pattern")
        )
        assert isinstance(result, MemorySearchResult)
        assert result.total_count >= 0

    async def test_search_without_retrieval_service_uses_fallback(
        self, service: MemoryBankService
    ) -> None:
        """When no retrieval_service is configured, fallback LIKE search is used."""
        assert service._retrieval_service is None
        req = _create_request(
            content=MemoryContent(
                title="Pydantic patterns", body="Advanced Pydantic v2 usage"
            )
        )
        await service.create_entry_async(req)
        result = await service.search_async(
            MemorySearchRequest(workspace_id="ws-test", text_query="pydantic")
        )
        assert result.total_count >= 1
        hit = result.items[0]
        assert hit.score == 1.0
        assert hit.rank >= 1
        assert "pydantic" in hit.entry.content_title.lower()

    async def test_search_with_retrieval_service_uses_fts(
        self, service: MemoryBankService
    ) -> None:
        """When a retrieval_service IS configured, the FTS5 path is used."""
        from relay_teams.retrieval.retrieval_models import (
            RetrievalHit,
        )

        # Create entry first so summary data exists
        req = _create_request(
            content=MemoryContent(title="FastAPI tips", body="Use dependency injection")
        )
        entry = await service.create_entry_async(req)

        # Build a mock retrieval service
        mock_retrieval = MagicMock()
        mock_retrieval.search_async = AsyncMock(
            return_value=[
                RetrievalHit(
                    document_id=entry.id,
                    title="FastAPI tips",
                    snippet="...FastAPI...",
                    score=0.85,
                    rank=1,
                )
            ]
        )
        service._retrieval_service = mock_retrieval

        result = await service.search_async(
            MemorySearchRequest(workspace_id="ws-test", text_query="fastapi")
        )
        assert result.total_count >= 1
        assert result.items[0].entry.id == entry.id
        assert result.items[0].score == 0.85

    async def test_search_fts_no_hits_returns_empty(
        self, service: MemoryBankService
    ) -> None:
        """FTS5 path with zero hits returns empty result."""
        mock_retrieval = MagicMock()
        mock_retrieval.search_async = AsyncMock(return_value=[])
        service._retrieval_service = mock_retrieval

        result = await service.search_async(
            MemorySearchRequest(workspace_id="ws-test", text_query="nope")
        )
        assert result.total_count == 0
        assert len(result.items) == 0

    async def test_build_snippet_short_body(self) -> None:
        """_build_snippet returns body preview when query not found."""
        result = MemoryBankService._build_snippet("Short body text", "missing")
        assert result == "Short body text"

    async def test_build_snippet_highlights_match(self) -> None:
        """_build_snippet extracts context around the match."""
        body = "A" * 100 + "TARGET" + "B" * 100
        result = MemoryBankService._build_snippet(body, "target")
        assert "TARGET" in result


# ---------------------------------------------------------------------------
# Capacity limit enforcement
# ---------------------------------------------------------------------------


class TestCapacityEnforcement:
    """Tests for enforce_capacity which prunes oldest entries when limits exceeded."""

    async def test_enforce_capacity_returns_zero_when_below_limit(
        self, service: MemoryBankService
    ) -> None:
        """No pruning when entry count is below the capacity limit."""
        pruned = await service.enforce_capacity_async(
            workspace_id="ws-test",
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
        )
        # We have 0 persistent entries, well below 2000 limit
        assert pruned == 0

    async def test_enforce_capacity_prunes_working_entries(
        self, service: MemoryBankService
    ) -> None:
        """When WORKING entries exceed MAX_WORKING_PER_RUN limit, oldest are pruned."""
        from relay_teams.memory.memory_defaults import MAX_WORKING_PER_RUN

        # Create enough entries to hit the limit (using unique run_id)
        run_id = "run-cap-test"
        for i in range(MAX_WORKING_PER_RUN + 1):
            req = _create_request(run_id=run_id)
            await service.create_entry_async(req)

        # Verify capacity enforcement happened during create
        result = await service.list_entries_async(
            MemoryQuery(
                workspace_id="ws-test",
                tier=MemoryTier.WORKING,
                status=MemoryEntryStatus.ACTIVE,
                limit=100,
            )
        )
        active_count = result.total_count
        assert active_count <= MAX_WORKING_PER_RUN

    async def test_enforce_capacity_does_not_affect_different_tier(
        self, service: MemoryBankService
    ) -> None:
        """Creating many WORKING entries should not prune PERSISTENT entries."""
        # Create a persistent entry
        req_p = _create_request(
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
        )
        persistent = await service.create_entry_async(req_p)

        # Create many working entries
        for i in range(10):
            req_w = _create_request(run_id=f"run-{i}")
            await service.create_entry_async(req_w)

        # Persistent entry should still exist and be active
        loaded = await service.get_entry_async(persistent.id)
        assert loaded is not None
        assert loaded.status == MemoryEntryStatus.ACTIVE
        assert loaded.tier == MemoryTier.PERSISTENT

    async def test_enforce_capacity_prunes_by_age(
        self, service: MemoryBankService
    ) -> None:
        """When PERSISTENT entries exceed capacity, oldest are expired first."""
        from relay_teams.memory import memory_defaults

        original_limit = memory_defaults.MAX_PERSISTENT_PER_WORKSPACE
        try:
            # Set a very low limit for testing
            memory_defaults.MAX_PERSISTENT_PER_WORKSPACE = 3
            ids: list[str] = []
            for i in range(5):
                req = _create_request(
                    tier=MemoryTier.PERSISTENT,
                    scope=MemoryScope.WORKSPACE,
                )
                entry = await service.create_entry_async(req)
                ids.append(entry.id)

            # Count active entries -- should be <= 3
            result = await service.list_entries_async(
                MemoryQuery(
                    workspace_id="ws-test",
                    tier=MemoryTier.PERSISTENT,
                    status=MemoryEntryStatus.ACTIVE,
                    limit=100,
                )
            )
            assert result.total_count <= 3
        finally:
            memory_defaults.MAX_PERSISTENT_PER_WORKSPACE = original_limit


# ---------------------------------------------------------------------------
# Condensation (placeholder)
# ---------------------------------------------------------------------------


class TestCondensation:
    async def test_condense_raises_not_implemented(
        self, service: MemoryBankService
    ) -> None:
        """Condensation is a placeholder that raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            service.condense("ws-test")
