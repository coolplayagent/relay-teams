# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from relay_teams.memory.models import (
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEntrySummary,
    MemoryQuery,
    MemoryQueryResult,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.memory_injection import (
    _build_role_evolution_section_async,
    _count_applied_adjustments_async,
    _find_latest_maturity_level_async,
    build_role_with_memory_async,
)
from relay_teams.roles.memory_models import (
    MemoryProfile,
    RolePerformanceMetrics,
    RoleTaskCounts,
    VerificationPassRate,
)
from relay_teams.roles.memory_service import RoleMemoryService
from relay_teams.roles.role_models import RoleDefinition
from relay_teams.roles.role_registry import RoleRegistry

pytestmark = pytest.mark.asyncio


def _make_role(
    *,
    role_id: str = "test-role",
    system_prompt: str = "## strategy\nBe helpful.",
    memory_enabled: bool = True,
) -> RoleDefinition:
    return RoleDefinition(
        role_id=role_id,
        name="Test Role",
        description="A test role.",
        version="1.0",
        system_prompt=system_prompt,
        memory_profile=MemoryProfile(enabled=memory_enabled),
    )


def _make_mock_registry(is_coordinator: bool = False) -> RoleRegistry:
    mock = MagicMock(spec=RoleRegistry)
    mock.is_coordinator_role.return_value = is_coordinator
    return mock


def _make_summary(
    *,
    entry_id: str = "me-1",
    title: str = "test",
) -> MemoryEntrySummary:
    now = datetime.now(timezone.utc)
    return MemoryEntrySummary(
        id=entry_id,
        tier=MemoryTier.PERSISTENT,
        scope=MemoryScope.ROLE,
        workspace_id="ws-1",
        session_id=None,
        role_id="role-1",
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        content_title=title,
        content_body_preview="test body",
        tags=(),
        confidence_score=0.8,
        source=MemorySourceKind.REFLECTION,
        version=1,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


async def test_build_role_with_memory_includes_evolution_section() -> None:
    perf = RolePerformanceMetrics(
        role_id="test-role",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=30,
            passed_verifications=20,
            pass_rate=0.67,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=30,
            successful_tasks=24,
            failed_tasks=6,
        ),
        average_verification_score=3.8,
    )
    mock_memory_service = MagicMock(spec=RoleMemoryService)
    mock_memory_service.build_injected_memory_async.return_value = (
        "- Learn: concise output"
    )
    mock_memory_service.get_performance_metrics_async.return_value = perf

    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(),
        total_count=0,
        offset=0,
        limit=10,
    )

    mock_registry = _make_mock_registry(is_coordinator=False)
    role = _make_role()

    result = await build_role_with_memory_async(
        role_registry=mock_registry,
        role_memory_service=mock_memory_service,
        memory_bank_service=mock_mbs,
        role=role,
        role_id="test-role",
        workspace_id="ws-1",
    )

    assert "## Role Evolution" in result.system_prompt
    assert "67.0%" in result.system_prompt
    assert "(20/30" in result.system_prompt
    assert "24 successful" in result.system_prompt
    assert "6 failed" in result.system_prompt
    assert "3.8/5.0" in result.system_prompt


async def test_build_role_with_memory_no_evolution_section() -> None:
    mock_memory_service = MagicMock(spec=RoleMemoryService)
    mock_memory_service.build_injected_memory_async.return_value = (
        "- Learn: concise output"
    )
    mock_memory_service.get_performance_metrics_async.return_value = None

    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_registry = _make_mock_registry(is_coordinator=False)
    role = _make_role()

    result = await build_role_with_memory_async(
        role_registry=mock_registry,
        role_memory_service=mock_memory_service,
        memory_bank_service=mock_mbs,
        role=role,
        role_id="test-role",
        workspace_id="ws-1",
    )

    assert "## Role Evolution" not in result.system_prompt
    assert "## Reflection Memory" in result.system_prompt


async def test_find_latest_maturity_level_with_maturity_entry() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(_make_summary(title="Maturity Scored - Level: L3"),),
        total_count=1,
        offset=0,
        limit=10,
    )
    result = await _find_latest_maturity_level_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result == "L3"


async def test_find_latest_maturity_level_no_match() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(_make_summary(title="Other insight"),),
        total_count=1,
        offset=0,
        limit=10,
    )
    result = await _find_latest_maturity_level_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result is None


async def test_find_latest_maturity_level_l_prefix() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(_make_summary(title="L5 assessment"),),
        total_count=1,
        offset=0,
        limit=10,
    )
    result = await _find_latest_maturity_level_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result is not None
    assert "L5" in result


async def test_find_latest_maturity_level_service_error() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.side_effect = RuntimeError("db error")
    result = await _find_latest_maturity_level_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result is None


async def test_count_applied_adjustments_with_matches() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(
            _make_summary(entry_id="me-0", title="prompt_applied v1"),
            _make_summary(entry_id="me-1", title="Adjustment applied v2"),
        ),
        total_count=2,
        offset=0,
        limit=50,
    )
    result = await _count_applied_adjustments_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result == 2


async def test_count_applied_adjustments_service_error() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.side_effect = ValueError("err")
    result = await _count_applied_adjustments_async(
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert result is None


async def test_build_role_evolution_section_with_maturity_and_adjustments() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)

    def mock_list(query: object) -> MemoryQueryResult:
        q = query if isinstance(query, MemoryQuery) else None
        if q is not None and q.limit == 10:
            return MemoryQueryResult(
                items=(_make_summary(title="Maturity Scored - Level: L4"),),
                total_count=1,
                offset=0,
                limit=10,
            )
        return MemoryQueryResult(
            items=(_make_summary(title="prompt_applied v1"),),
            total_count=1,
            offset=0,
            limit=50,
        )

    mock_mbs.list_entries_async.side_effect = mock_list
    perf = RolePerformanceMetrics(
        role_id="role-1",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=100,
            passed_verifications=75,
            pass_rate=0.75,
        ),
        task_counts=RoleTaskCounts(
            total_tasks=50,
            successful_tasks=40,
            failed_tasks=10,
        ),
        average_verification_score=3.8,
    )
    result = await _build_role_evolution_section_async(
        performance=perf,
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert "L4" in result
    assert "75.0%" in result
    assert "Prompt Adjustments Applied" in result


async def test_build_role_evolution_section_no_maturity_no_adjustments() -> None:
    mock_mbs = MagicMock(spec=MemoryBankService)
    mock_mbs.list_entries_async.return_value = MemoryQueryResult(
        items=(),
        total_count=0,
        offset=0,
        limit=10,
    )
    perf = RolePerformanceMetrics(
        role_id="role-1",
        workspace_id="ws-1",
        verification_pass_rate=VerificationPassRate(
            total_verifications=10,
            passed_verifications=8,
            pass_rate=0.8,
        ),
        task_counts=RoleTaskCounts(total_tasks=10, successful_tasks=8, failed_tasks=2),
        average_verification_score=4.0,
    )
    result = await _build_role_evolution_section_async(
        performance=perf,
        memory_bank_service=mock_mbs,
        workspace_id="ws-1",
        role_id="role-1",
    )
    assert "80.0%" in result
    # No maturity line when query returns empty
    assert "Maturity Level" not in result
