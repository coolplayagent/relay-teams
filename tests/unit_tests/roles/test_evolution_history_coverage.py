# -*- coding: utf-8 -*-
"""Coverage for evolution_history.py missing lines."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from relay_teams.roles.evolution_history import RoleEvolutionHistoryService
from relay_teams.roles.maturity_scoring import MaturityLevel

pytestmark = pytest.mark.asyncio


def _make_service() -> tuple[RoleEvolutionHistoryService, MagicMock, MagicMock]:
    adj_repo = MagicMock()
    adj_repo.list_decisions.return_value = ()
    bank = MagicMock()
    bank.create_entry_async = AsyncMock()
    bank.list_entries_async = AsyncMock(
        return_value=MagicMock(items=(), total_count=0, offset=0, limit=50)
    )
    bank.get_entry_async = AsyncMock(return_value=None)
    return (
        RoleEvolutionHistoryService(
            adjustment_repository=adj_repo,
            memory_bank_service=bank,
        ),
        adj_repo,
        bank,
    )


async def test_get_timeline_returns_empty() -> None:
    svc, _, bank = _make_service()
    timeline = await svc.get_timeline_async(role_id="r1", workspace_id="w1")
    assert timeline.role_id == "r1"
    assert timeline.events == ()


async def test_get_current_state_no_decisions() -> None:
    svc, adj_repo, bank = _make_service()
    adj_repo.list_decisions.return_value = ()
    state = await svc.get_current_state_async(role_id="r1", workspace_id="w1")
    assert state.current_prompt_version == 1
    assert state.current_maturity_level is None
    assert state.lifetime_adjustment_count == 0


async def test_record_event_with_maturity_level() -> None:
    svc, adj_repo, bank = _make_service()
    evt = await svc.record_event_async(
        role_id="r1",
        workspace_id="w1",
        event_type="maturity_scored",
        summary="Scored L3",
        maturity_level_before=MaturityLevel.L2_TASK_ORIENTED,
        maturity_level_after=MaturityLevel.L3_CONTEXT_AWARE,
    )
    assert evt.event_type == "maturity_scored"
    assert evt.maturity_level_before == MaturityLevel.L2_TASK_ORIENTED
    assert evt.maturity_level_after == MaturityLevel.L3_CONTEXT_AWARE
    bank.create_entry_async.assert_awaited_once()


async def test_record_event_without_maturity() -> None:
    svc, _, bank = _make_service()
    evt = await svc.record_event_async(
        role_id="r2",
        workspace_id="w1",
        event_type="prompt_proposed",
        summary="Proposed change",
    )
    assert evt.event_type == "prompt_proposed"
    assert evt.maturity_level_before is None
    assert evt.maturity_level_after is None
