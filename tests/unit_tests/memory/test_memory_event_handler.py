# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.agents.tasks.models import VerificationReport
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

    async def test_includes_verification_outcome_in_memory_entry(
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
            objective="Test",
            result="OK",
            verification_report=VerificationReport(
                task_id="task-1",
                passed=True,
                checks=(),
            ),
        )
        result = await memory_bank_service.list_entries_async(
            MemoryQuery(workspace_id="ws-1", tier=MemoryTier.WORKING, limit=10)
        )
        entry = await memory_bank_service.get_entry_async(result.items[0].id)
        assert entry is not None
        assert entry.content.outcome == "completed verification=passed"


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
