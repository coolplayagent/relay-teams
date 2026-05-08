# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from relay_teams.roles.prompt_adjustment_engine import SystemPromptAdjustmentEngine
from relay_teams.roles.temporary_knowledge_capture import (
    TemporaryRoleKnowledgeCaptureService,
    _extract_additions,
)
from relay_teams.roles.temporary_role_models import (
    TemporaryRoleRecord,
    TemporaryRoleSource,
    TemporaryRoleSpec,
)
from relay_teams.roles.temporary_role_repository import TemporaryRoleRepository


def _make_temp_record(
    *,
    role_id: str = "temp-1",
    run_id: str = "run-1",
    template_role_id: str | None = "template-1",
) -> TemporaryRoleRecord:
    return TemporaryRoleRecord(
        run_id=run_id,
        session_id="sess-1",
        source=TemporaryRoleSource.META_AGENT_GENERATED,
        role=TemporaryRoleSpec(
            role_id=role_id,
            name="Temp Role",
            description="Test",
            system_prompt="Be helpful",
            template_role_id=template_role_id,
        ),
    )


@pytest.mark.asyncio
async def test_capture_returns_none_for_non_template() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record(template_role_id=None)
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
    )
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt="Be helpful\n\nAdded tip",
        original_prompt="Be helpful",
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is None
    mock_engine.propose_adjustment_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_returns_none_for_identical_prompt() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
    )
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt="Be helpful",
        original_prompt="Be helpful",
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_capture_detects_additions() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
        min_diff_chars=10,
    )
    effective = "Be helpful\n\n## Additional Strategy\nAlways verify before responding with results."
    original = "Be helpful"
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt=effective,
        original_prompt=original,
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is not None
    assert result.source_role_id == "temp-1"
    assert result.target_template_role_id == "template-1"
    assert result.effective_prompt == effective
    assert result.original_prompt == original
    assert len(result.prompt_diff_markdown) > 0

    mock_engine.propose_adjustment_async.assert_awaited_once()
    call_kwargs = mock_engine.propose_adjustment_async.await_args.kwargs
    assert call_kwargs["role_id"] == "template-1"
    assert call_kwargs["workspace_id"] == "ws-1"
    assert call_kwargs["trigger_source"] == "temporary_role_capture"


@pytest.mark.asyncio
async def test_capture_ignores_whitespace_only() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
        min_diff_chars=50,
    )
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt="  Be helpful  ",
        original_prompt="Be helpful",
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_capture_all_for_session() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
    )
    result = await service.capture_all_for_session(
        _session_id="sess-1",
        _workspace_id="ws-1",
    )
    assert result == ()


@pytest.mark.asyncio
async def test_capture_diff_with_removals_and_additions() -> None:
    """Cover _compute_prompt_diff_markdown when original content is replaced."""
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
        min_diff_chars=5,
    )
    effective = "Be very careful and thorough in all analysis tasks."
    original = "Be helpful and concise."
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt=effective,
        original_prompt=original,
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is not None
    # The diff should contain both removal and addition lines
    assert "-" in result.prompt_diff_markdown or "+" in result.prompt_diff_markdown
    mock_engine.propose_adjustment_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_below_min_diff_chars() -> None:
    """Cover the min_diff_chars threshold in _compute_prompt_diff_markdown."""
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
        min_diff_chars=500,
    )
    effective = "Be helpful\n\nSome small new addition here."
    original = "Be helpful"
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt=effective,
        original_prompt=original,
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is None


@pytest.mark.asyncio
async def test_capture_keyerror() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.side_effect = KeyError("not found")
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
    )
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt="Be helpful",
        original_prompt="Be helpful",
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is None
    mock_engine.propose_adjustment_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_capture_non_prefix_effective() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_repo.get_async.return_value = _make_temp_record()
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
        min_diff_chars=5,
    )
    effective = "Completely different prompt with substantial new content here."
    original = "Be helpful"
    result = await service.capture_on_subagent_stop(
        subagent_role_id="temp-1",
        subagent_run_id="run-1",
        effective_prompt=effective,
        original_prompt=original,
        workspace_id="ws-1",
        _session_id="sess-1",
    )
    assert result is not None
    assert result.prompt_diff_markdown != ""
    mock_engine.propose_adjustment_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_capture_all_for_session_returns_empty() -> None:
    mock_engine = MagicMock(spec=SystemPromptAdjustmentEngine)
    mock_repo = MagicMock(spec=TemporaryRoleRepository)
    mock_memory = MagicMock()

    service = TemporaryRoleKnowledgeCaptureService(
        adjustment_engine=mock_engine,
        temporary_role_repository=mock_repo,
        role_memory_service=mock_memory,
    )
    result = await service.capture_all_for_session(
        _session_id="sess-1",
        _workspace_id="ws-1",
    )
    assert result == ()


def test_extract_additions_prefix_match() -> None:
    """Cover _extract_additions when effective starts with original."""
    result = _extract_additions(
        "Be helpful\n\nAdditional strategy content here.",
        "Be helpful",
    )
    assert "Additional strategy content here." in result
    assert "Be helpful" not in result


def test_extract_additions_no_prefix() -> None:
    """Cover _extract_additions when effective does NOT start with original."""
    result = _extract_additions("Completely different prompt text.", "Be helpful")
    assert "Completely different prompt text." in result


def test_extract_additions_identical() -> None:
    """Cover _extract_additions when effective equals original (returns empty)."""
    result = _extract_additions("Be helpful", "Be helpful")
    assert result == ""


def test_extract_additions_with_blank_lines() -> None:
    """Cover _extract_additions filtering blank lines."""
    from relay_teams.roles.temporary_knowledge_capture import _extract_additions

    result = _extract_additions("Be helpful\n\n\n   \nLine after blanks", "Be helpful")
    assert "Line after blanks" in result


def test_compute_prompt_diff_returns_none_for_equal() -> None:
    from relay_teams.roles.temporary_knowledge_capture import _compute_prompt_diff

    result = _compute_prompt_diff(effective="Same", original="Same", min_diff_chars=1)
    assert result is None


def test_compute_prompt_diff_returns_none_below_threshold() -> None:
    from relay_teams.roles.temporary_knowledge_capture import _compute_prompt_diff

    result = _compute_prompt_diff(effective="A", original="B", min_diff_chars=100)
    assert result is None


def test_compute_prompt_diff_with_removals_and_additions() -> None:
    from relay_teams.roles.temporary_knowledge_capture import _compute_prompt_diff

    result = _compute_prompt_diff(
        effective="New content here",
        original="Old content here",
        min_diff_chars=1,
    )
    assert result is not None
    assert "-" in result
    assert "+" in result


def test_compute_prompt_diff_additions_only() -> None:
    from relay_teams.roles.temporary_knowledge_capture import _compute_prompt_diff

    result = _compute_prompt_diff(
        effective="Be helpful\n\nNew line added",
        original="Be helpful",
        min_diff_chars=1,
    )
    assert result is not None
    assert "+ New line added" in result
