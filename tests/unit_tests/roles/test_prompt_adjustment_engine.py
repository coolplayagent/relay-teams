# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from relay_teams.roles.prompt_adjustment_engine import (
    PromptAdjustmentRepository,
    PromptAdjustmentStatus,
    SystemPromptAdjustmentEngine,
    _merge_sections,
)
from relay_teams.roles.self_assessment_service import PromptAdjustmentRecommendation


@pytest.fixture
def repo(tmp_path: Path) -> PromptAdjustmentRepository:
    return PromptAdjustmentRepository(tmp_path / "test_pa.db")


@pytest.fixture
def mock_registry() -> MagicMock:
    return MagicMock()


def _make_rec(
    target: str = "strategy", text: str = "Improved strategy"
) -> PromptAdjustmentRecommendation:
    return PromptAdjustmentRecommendation(
        target_section=target,
        current_text="",
        recommended_text=text,
        rationale="Test recommendation",
        priority=3,
        confidence=0.5,
    )


def test_propose_adjustment_creates_proposed(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld strategy",
        recommendations=(_make_rec(text="Improved strategy"),),
        trigger_source="self_assessment",
        triggered_by="assessment-1",
    )
    assert decision.status == PromptAdjustmentStatus.PROPOSED
    assert decision.role_id == "test-role"
    assert decision.workspace_id == "ws-1"
    assert decision.previous_prompt == "## strategy\nOld strategy"
    assert "Improved strategy" in decision.proposed_prompt
    assert decision.trigger_source == "self_assessment"
    assert decision.triggered_by == "assessment-1"
    assert decision.version == 1


@pytest.mark.asyncio
async def test_async_prompt_adjustment_repository_and_engine(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    assert await repo.get_decision_async("missing") is None
    assert (
        await repo.get_latest_applied_async(role_id="test-role", workspace_id="ws-1")
        is None
    )

    engine = SystemPromptAdjustmentEngine(repository=repo, role_registry=mock_registry)
    first = await engine.propose_adjustment_async(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="Async v1"),),
        trigger_source="self_assessment",
        triggered_by="async-1",
    )
    applied = first.model_copy(
        update={
            "status": PromptAdjustmentStatus.APPLIED,
            "applied_at": datetime.now(tz=timezone.utc),
        }
    )
    _ = await repo.save_decision_async(applied)

    second = await engine.propose_adjustment_async(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nAsync v1",
        recommendations=(_make_rec(text="Async v2"),),
        trigger_source="self_assessment",
        triggered_by="async-2",
    )

    latest = await repo.get_latest_applied_async(
        role_id="test-role",
        workspace_id="ws-1",
    )
    retrieved = await repo.get_decision_async(second.decision_id)
    assert latest is not None
    assert latest.decision_id == first.decision_id
    assert retrieved is not None
    assert retrieved.version == 2
    assert second.version == 2


def test_approve_transitions_to_approved(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    approved = engine.approve_adjustment(
        decision_id=decision.decision_id,
        reviewed_by="admin",
    )
    assert approved.status == PromptAdjustmentStatus.APPROVED
    assert approved.reviewed_by == "admin"
    assert approved.reviewed_at is not None


def test_approve_on_non_proposed_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.approve_adjustment(decision_id=decision.decision_id, reviewed_by="admin")

    with pytest.raises(ValueError, match="not in PROPOSED status"):
        engine.approve_adjustment(decision_id=decision.decision_id, reviewed_by="admin")


def test_reject_transitions_to_rejected(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    rejected = engine.reject_adjustment(
        decision_id=decision.decision_id,
        reviewed_by="admin",
        reason="Not needed",
    )
    assert rejected.status == PromptAdjustmentStatus.REJECTED
    assert rejected.rejection_reason == "Not needed"


def test_apply_updates_system_prompt(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    from relay_teams.roles.role_models import RoleDefinition

    original_role = RoleDefinition(
        role_id="test-role",
        name="Test Role",
        description="A test role",
        system_prompt="## strategy\nOld strategy",
        version="1.0",
    )
    mock_registry.get.return_value = original_role

    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld strategy",
        recommendations=(_make_rec(text="Improved strategy"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.approve_adjustment(decision_id=decision.decision_id, reviewed_by="admin")

    engine.apply_adjustment(decision_id=decision.decision_id)

    mock_registry.get.assert_called_with("test-role")
    mock_registry.register.assert_called_once()
    registered_role = mock_registry.register.call_args[0][0]
    assert "Improved strategy" in registered_role.system_prompt


def test_rollback_restores_previous_prompt(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    from relay_teams.roles.role_models import RoleDefinition

    original_role = RoleDefinition(
        role_id="test-role",
        name="Test Role",
        description="A test role",
        system_prompt="## strategy\nOld strategy",
        version="1.0",
    )
    mock_registry.get.return_value = original_role

    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld strategy",
        recommendations=(_make_rec(text="Improved strategy"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.approve_adjustment(decision_id=decision.decision_id, reviewed_by="admin")
    engine.apply_adjustment(decision_id=decision.decision_id)

    engine.rollback_adjustment(
        decision_id=decision.decision_id,
        reason="Rolled back for testing",
    )

    registered_role = mock_registry.register.call_args[0][0]
    assert "Old strategy" in registered_role.system_prompt


def test_section_merging_replaces_existing() -> None:
    current = "## strategy\nOld strategy\n\n## constraints\nBe safe"
    rec = (_make_rec(target="strategy", text="New strategy content"),)
    result = _merge_sections(current, rec)
    assert "New strategy content" in result
    assert "Old strategy" not in result
    assert "Be safe" in result


def test_section_merging_appends_new() -> None:
    current = "## strategy\nBe helpful"
    rec = (_make_rec(target="verification", text="Verify all outputs"),)
    result = _merge_sections(current, rec)
    assert "Be helpful" in result
    assert "Verify all outputs" in result
    assert "## verification" in result


def test_get_latest_applied(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    from relay_teams.roles.role_models import RoleDefinition

    original_role = RoleDefinition(
        role_id="test-role",
        name="Test Role",
        description="A test role",
        system_prompt="## strategy\nOld",
        version="1.0",
    )
    mock_registry.get.return_value = original_role

    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    d1 = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="V1"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.approve_adjustment(decision_id=d1.decision_id, reviewed_by="admin")
    engine.apply_adjustment(decision_id=d1.decision_id)

    latest = repo.get_latest_applied(role_id="test-role", workspace_id="ws-1")
    assert latest is not None
    assert latest.decision_id == d1.decision_id


def test_get_decision(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    retrieved = repo.get_decision(decision.decision_id)
    assert retrieved is not None
    assert retrieved.decision_id == decision.decision_id
    assert retrieved.status == PromptAdjustmentStatus.PROPOSED


def test_list_decisions_no_filter(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    decisions = repo.list_decisions(role_id="test-role", workspace_id="ws-1")
    assert len(decisions) == 1


def test_list_decisions_with_status_filter(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    decisions = repo.list_decisions(
        role_id="test-role",
        workspace_id="ws-1",
        status=PromptAdjustmentStatus.REJECTED,
    )
    assert len(decisions) == 0


def test_get_latest_applied_none(
    repo: PromptAdjustmentRepository,
) -> None:
    result = repo.get_latest_applied(role_id="nonexistent", workspace_id="ws-1")
    assert result is None


def test_get_decision_missing(
    repo: PromptAdjustmentRepository,
) -> None:
    result = repo.get_decision("nonexistent")
    assert result is None


def test_approve_unknown_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    with pytest.raises(ValueError, match="Unknown decision"):
        engine.approve_adjustment(decision_id="missing", reviewed_by="admin")


def test_apply_non_approved_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    with pytest.raises(ValueError, match="not in APPROVED status"):
        engine.apply_adjustment(decision_id=decision.decision_id)


def test_rollback_non_applied_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(
        repository=repo,
        role_registry=mock_registry,
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    with pytest.raises(ValueError, match="not in APPLIED status"):
        engine.rollback_adjustment(decision_id=decision.decision_id, reason="test")


def test_version_increments(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    from relay_teams.roles.role_models import RoleDefinition

    role = RoleDefinition(
        role_id="test-role",
        name="T",
        description="t",
        system_prompt="## strategy\nOld",
        version="1.0",
    )
    mock_registry.get.return_value = role

    engine = SystemPromptAdjustmentEngine(repository=repo, role_registry=mock_registry)
    d1 = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="V1"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.approve_adjustment(decision_id=d1.decision_id, reviewed_by="admin")
    engine.apply_adjustment(decision_id=d1.decision_id)

    d2 = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nV1",
        recommendations=(_make_rec(text="V2"),),
        trigger_source="self_assessment",
        triggered_by="a2",
    )
    assert d2.version == 2


def test_merge_sections_with_preamble() -> None:
    current = "This is a preamble\n\n## strategy\nBe helpful"
    rec = (_make_rec(target="strategy", text="New strategy"),)
    result = _merge_sections(current, rec)
    assert "This is a preamble" in result
    assert "New strategy" in result


def test_merge_sections_empty_prompt() -> None:
    result = _merge_sections("", (_make_rec(target="strategy", text="New"),))
    assert "New" in result


def test_merge_sections_with_recommendations_in_decision(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(repository=repo, role_registry=mock_registry)
    recs = (
        _make_rec(target="strategy", text="New strat"),
        _make_rec(target="constraints", text="New constraints"),
    )
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld\n\n## constraints\nOld c",
        recommendations=recs,
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    assert "New strat" in decision.proposed_prompt
    assert "New constraints" in decision.proposed_prompt


def test_reject_on_non_proposed_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(repository=repo, role_registry=mock_registry)
    decision = engine.propose_adjustment(
        role_id="test-role",
        workspace_id="ws-1",
        current_prompt="## strategy\nOld",
        recommendations=(_make_rec(text="New"),),
        trigger_source="self_assessment",
        triggered_by="a1",
    )
    engine.reject_adjustment(
        decision_id=decision.decision_id, reviewed_by="admin", reason="bad"
    )
    with pytest.raises(ValueError, match="not in PROPOSED status"):
        engine.reject_adjustment(
            decision_id=decision.decision_id, reviewed_by="admin", reason="bad2"
        )


def test_rollback_unknown_raises(
    repo: PromptAdjustmentRepository,
    mock_registry: MagicMock,
) -> None:
    engine = SystemPromptAdjustmentEngine(repository=repo, role_registry=mock_registry)
    with pytest.raises(ValueError, match="Unknown decision"):
        engine.rollback_adjustment(decision_id="missing", reason="test")


def test_merge_sections_no_headers() -> None:
    """Cover _merge_sections with a prompt that has no ## headers at all."""
    current = "Just plain text with no sections."
    rec = (_make_rec(target="strategy", text="New strategy"),)
    result = _merge_sections(current, rec)
    assert "New strategy" in result


def test_merge_sections_header_no_trailing_newline() -> None:
    """Cover when remaining ends without newline after last header."""
    current = "## strategy\nOld"
    rec = (_make_rec(target="strategy", text="New"),)
    result = _merge_sections(current, rec)
    assert "New" in result


def test_merge_sections_multiple_recs_one_new() -> None:
    """Replace existing section and append new section in one pass."""
    current = "## strategy\nBe helpful\n\n## constraints\nBe safe"
    recs = (
        _make_rec(target="strategy", text="New strategy"),
        _make_rec(target="verification", text="Always verify"),
    )
    result = _merge_sections(current, recs)
    assert "New strategy" in result
    assert "Always verify" in result
    assert "Be safe" in result


def test_merge_sections_consecutive_headers() -> None:
    """Cover consecutive ## headers with no body between them."""
    current = "## strategy\n## constraints\nBe safe"
    rec = (_make_rec(target="constraints", text="New constraints"),)
    result = _merge_sections(current, rec)
    assert "New constraints" in result


def test_merge_sections_header_at_start() -> None:
    """Cover prompt starting with ## (no preamble)."""
    current = "## strategy\nContent"
    rec = (_make_rec(target="strategy", text="Replaced"),)
    result = _merge_sections(current, rec)
    assert "Replaced" in result
    assert "__preamble__" not in result
