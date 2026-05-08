# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.memory.models import (
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEntrySummary,
    MemoryQuery,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_service import RoleMemoryService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repo(tmp_path: Path) -> MemoryBankRepository:
    return MemoryBankRepository(tmp_path / "test_memory.db")


@pytest.fixture
def memory_bank_service(repo: MemoryBankRepository) -> MemoryBankService:
    return MemoryBankService(repository=repo)


@pytest.fixture
def handler(
    memory_bank_service: MemoryBankService,
) -> MemoryEventHandler:
    return MemoryEventHandler(
        memory_bank_service=memory_bank_service,
        role_memory_service=None,
    )


@pytest.fixture
def handler_with_dual_write(
    memory_bank_service: MemoryBankService,
) -> MemoryEventHandler:
    mock_role_memory = MagicMock(spec=RoleMemoryService)
    return MemoryEventHandler(
        memory_bank_service=memory_bank_service,
        role_memory_service=mock_role_memory,
    )


async def _list_entries(
    svc: MemoryBankService,
    workspace_id: str,
    tier: MemoryTier,
) -> list[MemoryEntrySummary]:
    result = await svc.list_entries_async(
        MemoryQuery(workspace_id=workspace_id, tier=tier, limit=100)
    )
    return list(result.items)


class TestOnTaskCompleted:
    async def test_creates_working_entry(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        await handler.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-1",
            objective="Fix login bug",
            result="Fixed null pointer in auth module",
        )
        entries = await _list_entries(memory_bank_service, "ws-1", MemoryTier.WORKING)
        assert len(entries) == 1
        e = entries[0]
        assert e.kind == MemoryEntryKind.SUMMARY
        assert e.source == MemorySourceKind.TASK_RESULT
        assert e.scope == MemoryScope.WORKSPACE
        assert e.role_id == "role-1"
        assert e.session_id == "sess-1"
        assert e.status == MemoryEntryStatus.ACTIVE
        assert "Fix login bug" in e.content_title

    async def test_dual_writes_to_role_memories(
        self,
        handler_with_dual_write: MemoryEventHandler,
    ) -> None:
        await handler_with_dual_write.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-1",
            objective="Build feature",
            result="Implemented feature X",
        )
        rm = handler_with_dual_write._role_memory_service
        assert rm is not None
        rm.record_task_result_async.assert_awaited_once_with(  # type: ignore[attr-defined]
            role_id="role-1",
            workspace_id="ws-1",
            session_id="sess-1",
            task_id="task-1",
            objective="Build feature",
            result="Implemented feature X",
            transcript_lines=(),
        )

    async def test_dual_write_failure_does_not_block_entry(
        self,
        memory_bank_service: MemoryBankService,
    ) -> None:
        mock_rm = MagicMock(spec=RoleMemoryService)
        mock_rm.record_task_result_async = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        h = MemoryEventHandler(
            memory_bank_service=memory_bank_service,
            role_memory_service=mock_rm,
        )
        await h.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-2",
            objective="Test",
            result="OK",
        )
        entries = await _list_entries(memory_bank_service, "ws-1", MemoryTier.WORKING)
        assert len(entries) == 1


class TestOnRunCompleted:
    async def test_consolidates_working_to_medium_term(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        # Create some WORKING entries first
        for i in range(3):
            await handler.on_task_completed_async(
                workspace_id="ws-1",
                role_id="role-1",
                session_id="sess-1",
                run_id="run-1",
                task_id=f"task-{i}",
                objective=f"Objective {i}",
                result=f"Result {i}",
            )
        # Verify entries are WORKING
        working = await _list_entries(memory_bank_service, "ws-1", MemoryTier.WORKING)
        assert len(working) == 3

        # Consolidate on run completion
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="role-1",
        )
        # After consolidation, entries should be promoted to MEDIUM_TERM
        medium = await _list_entries(
            memory_bank_service, "ws-1", MemoryTier.MEDIUM_TERM
        )
        assert len(medium) >= 1


class TestOnSessionCompleted:
    async def test_consolidates_medium_to_persistent(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        # Create and promote to MEDIUM_TERM via run consolidation
        for i in range(2):
            await handler.on_task_completed_async(
                workspace_id="ws-1",
                role_id="role-1",
                session_id="sess-1",
                run_id="run-1",
                task_id=f"task-{i}",
                objective=f"Obj {i}",
                result=f"Res {i}",
            )
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="role-1",
        )

        # Now consolidate on session completion
        await handler.on_session_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="role-1",
        )

        persistent = await _list_entries(
            memory_bank_service, "ws-1", MemoryTier.PERSISTENT
        )
        assert len(persistent) >= 1


class TestGetInjectableMemoryText:
    async def test_returns_empty_for_no_entries(
        self,
        handler: MemoryEventHandler,
    ) -> None:
        text = await handler.get_injectable_memory_text_async(
            workspace_id="ws-empty",
            role_id="role-1",
        )
        assert text == ""

    async def test_returns_text_for_persistent_entries(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        # Create and promote entries to PERSISTENT
        await handler.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-1",
            objective="Important insight",
            result="Discovered X",
        )
        await handler.on_run_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="role-1",
        )
        await handler.on_session_completed_async(
            workspace_id="ws-1",
            session_id="sess-1",
            role_id="role-1",
        )

        text = await handler.get_injectable_memory_text_async(
            workspace_id="ws-1",
            role_id="role-1",
        )
        assert len(text) > 0
