# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from relay_teams.memory.models import (
    MemoryContent,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEntrySummary,
    MemoryQueryResult,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.roles.evolution_history import RoleEvolutionHistoryService
from relay_teams.roles.prompt_adjustment_engine import PromptAdjustmentRepository

pytestmark = pytest.mark.asyncio


def _make_memory_entry(
    *,
    entry_id: str = "evt-1",
    role_id: str = "test-role",
    workspace_id: str = "ws-1",
    event_type: str = "prompt_applied",
    body: str = "Applied improvement to strategy section",
) -> MemoryEntry:
    import json
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    context = json.dumps(
        {
            "event_type": event_type,
            "trigger_source": "self_assessment",
            "decision_id": "pad-1",
            "maturity_level_before": None,
            "maturity_level_after": None,
        }
    )
    return MemoryEntry(
        id=entry_id,
        tier=MemoryTier.MEDIUM_TERM,
        scope=MemoryScope.ROLE,
        workspace_id=workspace_id,
        role_id=role_id,
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        content=MemoryContent(
            title=f"Role Evolution: {event_type}",
            body=body,
            context=context,
        ),
        source=MemorySourceKind.REFLECTION,
        source_ref=f"rev-{entry_id}",
        created_at=now,
        updated_at=now,
    )


def _make_memory_summary(
    *,
    entry_id: str = "evt-1",
    role_id: str = "test-role",
    workspace_id: str = "ws-1",
) -> MemoryEntrySummary:
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc)
    return MemoryEntrySummary(
        id=entry_id,
        tier=MemoryTier.MEDIUM_TERM,
        scope=MemoryScope.ROLE,
        workspace_id=workspace_id,
        session_id="sess-1",
        role_id=role_id,
        kind=MemoryEntryKind.INSIGHT,
        status=MemoryEntryStatus.ACTIVE,
        content_title="Role Evolution: prompt_applied",
        content_body_preview="Applied strategy improvement",
        tags=("role_evolution",),
        confidence_score=0.5,
        source=MemorySourceKind.REFLECTION,
        version=1,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


async def test_record_event_creates_memory_entry() -> None:
    mock_adjustment = MagicMock(spec=PromptAdjustmentRepository)
    mock_bank = MagicMock(spec=MemoryBankService)

    service = RoleEvolutionHistoryService(
        adjustment_repository=mock_adjustment,
        memory_bank_service=mock_bank,
    )
    event = await service.record_event_async(
        role_id="test-role",
        workspace_id="ws-1",
        event_type="prompt_applied",
        summary="Applied improvement to strategy section",
        trigger_source="self_assessment",
        decision_id="pad-1",
    )
    assert event.event_id.startswith("rev-")
    assert event.role_id == "test-role"
    assert event.workspace_id == "ws-1"
    assert event.event_type == "prompt_applied"
    assert event.summary == "Applied improvement to strategy section"
    assert event.trigger_source == "self_assessment"
    assert event.decision_id == "pad-1"

    mock_bank.create_entry_async.assert_awaited_once()
    call_args = mock_bank.create_entry_async.call_args[0][0]
    assert call_args.scope == MemoryScope.ROLE
    assert call_args.kind == MemoryEntryKind.INSIGHT
    assert call_args.source == MemorySourceKind.REFLECTION


async def test_get_timeline_returns_events() -> None:
    mock_adjustment = MagicMock(spec=PromptAdjustmentRepository)
    mock_adjustment.get_latest_applied.return_value = None
    mock_adjustment.list_decisions.return_value = ()
    mock_bank = MagicMock(spec=MemoryBankService)

    entry = _make_memory_entry(
        entry_id="evt-1",
        event_type="prompt_applied",
        body="Applied strategy improvement",
    )
    mock_bank.get_entry_async.return_value = entry
    mock_bank.list_entries_async.return_value = MemoryQueryResult(
        items=(_make_memory_summary(entry_id="evt-1"),),
        total_count=1,
        offset=0,
        limit=50,
    )

    service = RoleEvolutionHistoryService(
        adjustment_repository=mock_adjustment,
        memory_bank_service=mock_bank,
    )
    timeline = await service.get_timeline_async(
        role_id="test-role",
        workspace_id="ws-1",
    )
    assert timeline.role_id == "test-role"
    assert timeline.workspace_id == "ws-1"
    assert len(timeline.events) == 1
    assert timeline.events[0].event_type == "prompt_applied"
    assert timeline.events[0].summary == "Applied strategy improvement"


async def test_get_current_state() -> None:
    from relay_teams.roles.prompt_adjustment_engine import (
        PromptAdjustmentDecision,
        PromptAdjustmentStatus,
    )
    from datetime import datetime, timezone

    mock_adjustment = MagicMock(spec=PromptAdjustmentRepository)
    now = datetime.now(tz=timezone.utc)
    applied_decision = PromptAdjustmentDecision(
        decision_id="pad-1",
        role_id="test-role",
        workspace_id="ws-1",
        version=3,
        previous_prompt="## strategy\nV2",
        proposed_prompt="## strategy\nV3",
        status=PromptAdjustmentStatus.APPLIED,
        proposed_at=now,
    )
    mock_adjustment.get_latest_applied.return_value = applied_decision
    mock_adjustment.list_decisions.return_value = (applied_decision,)

    entry = _make_memory_entry(
        entry_id="evt-1",
        event_type="prompt_applied",
        body="Applied V3",
    )
    mock_bank = MagicMock(spec=MemoryBankService)
    mock_bank.get_entry_async.return_value = entry
    mock_bank.list_entries_async.return_value = MemoryQueryResult(
        items=(_make_memory_summary(entry_id="evt-1"),),
        total_count=1,
        offset=0,
        limit=100,
    )

    service = RoleEvolutionHistoryService(
        adjustment_repository=mock_adjustment,
        memory_bank_service=mock_bank,
    )
    state = await service.get_current_state_async(
        role_id="test-role",
        workspace_id="ws-1",
    )
    assert state.current_prompt_version == 3
    assert state.lifetime_adjustment_count == 1


async def test_event_links_decision_id() -> None:
    mock_adjustment = MagicMock(spec=PromptAdjustmentRepository)
    mock_bank = MagicMock(spec=MemoryBankService)

    service = RoleEvolutionHistoryService(
        adjustment_repository=mock_adjustment,
        memory_bank_service=mock_bank,
    )
    event = await service.record_event_async(
        role_id="test-role",
        workspace_id="ws-1",
        event_type="prompt_applied",
        summary="Applied improvement",
        decision_id="pad-abc123",
    )
    assert event.decision_id == "pad-abc123"

    call_args = mock_bank.create_entry_async.call_args[0][0]
    import json

    ctx = json.loads(call_args.content.context)
    assert ctx.get("decision_id") == "pad-abc123"
