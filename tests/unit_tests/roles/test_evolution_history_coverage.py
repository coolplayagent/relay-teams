# -*- coding: utf-8 -*-
"""Coverage for evolution_history.py missing lines."""

from __future__ import annotations

from unittest.mock import MagicMock


from relay_teams.roles.evolution_history import RoleEvolutionHistoryService
from relay_teams.roles.maturity_scoring import MaturityLevel


def _make_service() -> tuple[RoleEvolutionHistoryService, MagicMock, MagicMock]:
    adj_repo = MagicMock()
    adj_repo.list_decisions.return_value = ()
    bank = MagicMock()
    bank.create_entry = MagicMock()
    bank.list_entries = MagicMock(
        return_value=MagicMock(items=(), total_count=0, offset=0, limit=50)
    )
    bank.get_entry = MagicMock(return_value=None)
    return (
        RoleEvolutionHistoryService(
            adjustment_repository=adj_repo,
            memory_bank_service=bank,
        ),
        adj_repo,
        bank,
    )


def test_get_timeline_returns_empty() -> None:
    svc, _, bank = _make_service()
    timeline = svc.get_timeline(role_id="r1", workspace_id="w1")
    assert timeline.role_id == "r1"
    assert timeline.events == ()


def test_get_current_state_no_decisions() -> None:
    svc, adj_repo, bank = _make_service()
    adj_repo.list_decisions.return_value = ()
    state = svc.get_current_state(role_id="r1", workspace_id="w1")
    assert state.current_prompt_version == 1
    assert state.current_maturity_level is None
    assert state.lifetime_adjustment_count == 0


def test_record_event_with_maturity_level() -> None:
    svc, adj_repo, bank = _make_service()
    evt = svc.record_event(
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
    bank.create_entry.assert_called_once()


def test_record_event_without_maturity() -> None:
    svc, _, bank = _make_service()
    evt = svc.record_event(
        role_id="r2",
        workspace_id="w1",
        event_type="prompt_proposed",
        summary="Proposed change",
    )
    assert evt.event_type == "prompt_proposed"
    assert evt.maturity_level_before is None
    assert evt.maturity_level_after is None
