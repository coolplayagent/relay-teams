# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.memory.models import (
    ConsolidationMode,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryTier,
)


@pytest.fixture
def mock_memory_bank() -> MagicMock:
    svc = MagicMock()
    svc.consolidate_async = AsyncMock(
        return_value=MemoryConsolidationResult(
            source_entry_count=2,
            consolidated_entry_count=2,
            superseded_entry_ids=(),
            new_entry_ids=("id1", "id2"),
        )
    )
    svc.create_entry = MagicMock(return_value=None)
    return svc


@pytest.fixture
def handler(mock_memory_bank: MagicMock) -> MemoryEventHandler:
    return MemoryEventHandler(memory_bank_service=mock_memory_bank)


class TestOnRunCompletedAsync:
    @pytest.mark.asyncio
    async def test_structural_consolidation_runs(
        self, handler: MemoryEventHandler, mock_memory_bank: MagicMock
    ) -> None:
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="Crafter",
            run_id="run-1",
        )
        # Should have been called twice: once structural, once semantic
        assert mock_memory_bank.consolidate_async.call_count == 2
        # First call = structural
        first_req = mock_memory_bank.consolidate_async.call_args_list[0].args[0]
        assert first_req.target_tier == MemoryTier.MEDIUM_TERM

    @pytest.mark.asyncio
    async def test_semantic_consolidation_triggered(
        self, handler: MemoryEventHandler, mock_memory_bank: MagicMock
    ) -> None:
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            run_id="run-1",
        )
        # Second call = semantic
        second_req = mock_memory_bank.consolidate_async.call_args_list[1].args[0]
        assert second_req.consolidation_mode == ConsolidationMode.SEMANTIC
        assert second_req.source_run_id == "run-1"

    @pytest.mark.asyncio
    async def test_semantic_not_triggered_without_run_id(
        self, handler: MemoryEventHandler, mock_memory_bank: MagicMock
    ) -> None:
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            run_id=None,
        )
        assert mock_memory_bank.consolidate_async.call_count == 1

    @pytest.mark.asyncio
    async def test_semantic_failure_non_fatal(
        self, handler: MemoryEventHandler, mock_memory_bank: MagicMock
    ) -> None:
        call_count = 0

        async def _side_effect(
            req: MemoryConsolidationRequest,
        ) -> MemoryConsolidationResult:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("LLM error")
            return MemoryConsolidationResult(
                source_entry_count=1,
                consolidated_entry_count=1,
                superseded_entry_ids=(),
                new_entry_ids=("id1",),
            )

        mock_memory_bank.consolidate_async = AsyncMock(side_effect=_side_effect)
        # Should not raise
        await handler.on_run_completed_async(
            workspace_id="ws-1", session_id="sess-1", run_id="run-1"
        )

    @pytest.mark.asyncio
    async def test_structural_failure_non_fatal(
        self, handler: MemoryEventHandler, mock_memory_bank: MagicMock
    ) -> None:
        mock_memory_bank.consolidate_async = AsyncMock(side_effect=ValueError("bad"))
        # Should not raise
        await handler.on_run_completed_async(
            workspace_id="ws-1", session_id="sess-1", run_id="run-1"
        )
