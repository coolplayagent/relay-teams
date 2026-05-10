# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from relay_teams.agents.tasks.enums import VerificationLayer
from relay_teams.agents.tasks.models import VerificationCheckResult, VerificationReport
from relay_teams.memory.event_handler import MemoryEventHandler
from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
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
from relay_teams.roles.memory_models import (
    PerformanceTrendPoint,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)

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

    async def test_records_role_performance_memory_from_verification(
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
                passed=False,
                checks=(),
            ),
        )

        result = await memory_bank_service.list_entries_async(
            MemoryQuery(
                workspace_id="ws-1",
                scope=MemoryScope.ROLE,
                role_id="role-1",
                kind=MemoryEntryKind.INSIGHT,
                tags=("role-performance",),
                limit=10,
            )
        )
        entry = await memory_bank_service.get_entry_async(result.items[0].id)

        assert result.total_count == 1
        assert entry is not None
        metrics = RolePerformanceMetrics.model_validate_json(entry.content.body)
        assert metrics.role_id == "role-1"
        assert metrics.task_counts.total_tasks == 1
        assert metrics.task_counts.failed_tasks == 1
        assert metrics.verification_pass_rate.total_verifications == 1
        assert metrics.verification_pass_rate.passed_verifications == 0
        assert entry.kind == MemoryEntryKind.INSIGHT
        assert entry.scope == MemoryScope.ROLE
        assert "role-performance" in entry.tags

    async def test_updates_existing_role_performance_and_trims_trend(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        seed = RolePerformanceMetrics(
            role_id="role-1",
            workspace_id="ws-1",
            verification_pass_rate=VerificationPassRate(
                total_verifications=20,
                passed_verifications=10,
                pass_rate=0.5,
            ),
            task_counts=RoleTaskCounts(
                total_tasks=20,
                successful_tasks=10,
                failed_tasks=10,
            ),
            average_verification_score=2.5,
            trend=tuple(
                PerformanceTrendPoint(
                    recorded_at=now - timedelta(minutes=20 - index),
                    verification_pass_rate=0.5,
                    average_verification_score=2.5,
                    total_tasks_at_point=index + 1,
                )
                for index in range(20)
            ),
        )
        created = await memory_bank_service.create_entry_async(
            CreateMemoryEntryRequest(
                tier=MemoryTier.PERSISTENT,
                scope=MemoryScope.ROLE,
                workspace_id="ws-1",
                role_id="role-1",
                kind=MemoryEntryKind.INSIGHT,
                content=MemoryContent(
                    title="Role performance for role-1",
                    body=seed.model_dump_json(),
                ),
                tags=("role-performance",),
                source=MemorySourceKind.MANUAL,
            )
        )

        await handler.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-2",
            objective="Test",
            result="OK",
            verification_report=VerificationReport(
                task_id="task-2",
                passed=True,
                checks=(
                    VerificationCheckResult(
                        layer=VerificationLayer.STRUCTURE,
                        name="structure",
                        passed=True,
                    ),
                    VerificationCheckResult(
                        layer=VerificationLayer.BEHAVIOR,
                        name="behavior",
                        passed=False,
                    ),
                ),
            ),
        )

        updated = await memory_bank_service.get_entry_async(created.id)
        assert updated is not None
        metrics = RolePerformanceMetrics.model_validate_json(updated.content.body)
        assert metrics.task_counts.total_tasks == 21
        assert metrics.task_counts.successful_tasks == 11
        assert metrics.verification_pass_rate.total_verifications == 21
        assert metrics.verification_pass_rate.passed_verifications == 11
        assert metrics.average_verification_score == 2.5
        assert len(metrics.trend) == 20
        assert metrics.trend[0].total_tasks_at_point == 2
        assert metrics.trend[-1].total_tasks_at_point == 21

    async def test_skips_invalid_role_performance_memory(
        self,
        handler: MemoryEventHandler,
        memory_bank_service: MemoryBankService,
    ) -> None:
        await memory_bank_service.create_entry_async(
            CreateMemoryEntryRequest(
                tier=MemoryTier.PERSISTENT,
                scope=MemoryScope.ROLE,
                workspace_id="ws-1",
                role_id="role-1",
                kind=MemoryEntryKind.INSIGHT,
                content=MemoryContent(
                    title="Broken role performance",
                    body="not-json",
                ),
                tags=("role-performance",),
                source=MemorySourceKind.MANUAL,
            )
        )

        await handler.on_task_completed_async(
            workspace_id="ws-1",
            role_id="role-1",
            session_id="sess-1",
            run_id="run-1",
            task_id="task-3",
            objective="Test",
            result="OK",
            verification_report=VerificationReport(
                task_id="task-3",
                passed=True,
                checks=(),
            ),
        )

        result = await memory_bank_service.list_entries_async(
            MemoryQuery(
                workspace_id="ws-1",
                scope=MemoryScope.ROLE,
                role_id="role-1",
                kind=MemoryEntryKind.INSIGHT,
                tags=("role-performance",),
                limit=10,
            )
        )
        valid_metrics: list[RolePerformanceMetrics] = []
        invalid_bodies: list[str] = []
        for summary in result.items:
            entry = await memory_bank_service.get_entry_async(summary.id)
            assert entry is not None
            try:
                valid_metrics.append(
                    RolePerformanceMetrics.model_validate_json(entry.content.body)
                )
            except ValueError:
                invalid_bodies.append(entry.content.body)

        assert result.total_count == 2
        assert invalid_bodies == ["not-json"]
        assert len(valid_metrics) == 1
        assert valid_metrics[0].task_counts.successful_tasks == 1


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
