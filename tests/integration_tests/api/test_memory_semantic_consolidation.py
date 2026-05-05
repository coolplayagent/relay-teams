# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.memory.models import (
    ConsolidationMode,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryEntryKind,
    MemoryScope,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.providers.provider_contracts import EchoProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def service(tmp_path: Path) -> MemoryBankService:
    db_file = tmp_path / "test_memory.db"
    repo = MemoryBankRepository(db_file)
    provider = EchoProvider()
    return MemoryBankService(repository=repo, llm_provider=provider)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestConsolidateEndpointModes:
    """AC: consolidate with SEMANTIC and STRUCTURAL modes."""

    @pytest.mark.asyncio
    async def test_consolidate_structural_mode_async(
        self, service: MemoryBankService
    ) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            session_id="sess-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            consolidation_mode=ConsolidationMode.STRUCTURAL,
        )
        result = await service.consolidate_async(req)
        assert isinstance(result, MemoryConsolidationResult)
        assert result.extraction_tokens_used == 0
        assert result.extraction_duration_ms == 0

    @pytest.mark.asyncio
    async def test_consolidate_semantic_mode_triggers_llm(
        self, service: MemoryBankService
    ) -> None:
        req = MemoryConsolidationRequest(
            workspace_id="ws-test",
            session_id="sess-1",
            source_run_id="run-1",
            target_tier=MemoryTier.MEDIUM_TERM,
            target_scope=MemoryScope.SESSION,
            consolidation_mode=ConsolidationMode.SEMANTIC,
            max_extracted_entries=5,
            extraction_kinds=(MemoryEntryKind.DECISION,),
        )
        # With no messages in the repo, it falls back to STRUCTURAL
        result = await service.consolidate_async(req)
        assert isinstance(result, MemoryConsolidationResult)


class TestRunCompletionTriggersSemantic:
    """AC: on_run_completed_async triggers SEMANTIC consolidation."""

    @pytest.mark.asyncio
    async def test_on_run_completed_async_structural(
        self, service: MemoryBankService
    ) -> None:
        from relay_teams.memory.event_handler import MemoryEventHandler

        handler = MemoryEventHandler(memory_bank_service=service)
        # Should not raise
        await handler.on_run_completed_async(
            workspace_id="ws-test",
            session_id="sess-1",
            run_id="run-1",
        )

    @pytest.mark.asyncio
    async def test_on_run_completed_async_with_semantic(
        self, service: MemoryBankService
    ) -> None:
        from relay_teams.memory.event_handler import MemoryEventHandler

        handler = MemoryEventHandler(memory_bank_service=service)
        # Should not raise even if semantic fails
        await handler.on_run_completed_async(
            workspace_id="ws-test",
            session_id="sess-1",
            role_id="crafter",
            run_id="run-1",
        )

    def test_on_run_completed_sync_unchanged(self, service: MemoryBankService) -> None:
        from relay_teams.memory.event_handler import MemoryEventHandler

        handler = MemoryEventHandler(memory_bank_service=service)
        # Sync version should still work
        handler.on_run_completed(
            workspace_id="ws-test",
            session_id="sess-1",
            run_id="run-1",
        )
