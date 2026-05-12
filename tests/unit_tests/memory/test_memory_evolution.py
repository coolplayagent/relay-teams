# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import pytest

from relay_teams.memory.evolution_service import MemoryEvolutionService
from relay_teams.memory.models import (
    ApplyMemoryEvolutionDraftRequest,
    CreateMemoryEntryRequest,
    CreateMemoryEvolutionDraftRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEvolutionDraftQuery,
    MemoryEvolutionStatus,
    MemoryEvolutionTarget,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
    RejectMemoryEvolutionDraftRequest,
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.persistence.sqlite_repository import async_fetchone
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService

pytestmark = pytest.mark.asyncio


class _ReloadRecorder:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


async def _create_memory(
    service: MemoryBankService,
    *,
    workspace_id: str = "ws-evo",
    title: str = "Review loop SOP",
) -> str:
    entry = await service.create_entry_async(
        CreateMemoryEntryRequest(
            workspace_id=workspace_id,
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
            kind=MemoryEntryKind.INSIGHT,
            content=MemoryContent(
                title=title,
                body="Capture useful review feedback as a reusable SOP.",
            ),
            source=MemorySourceKind.MANUAL,
        )
    )
    return entry.id


def _build_services(
    tmp_path: Path,
) -> tuple[MemoryBankService, MemoryEvolutionService, _ReloadRecorder]:
    repository = MemoryBankRepository(tmp_path / "memory_evolution.db")
    memory_service = MemoryBankService(repository=repository)
    reload_recorder = _ReloadRecorder()
    evolution_service = MemoryEvolutionService(
        repository=repository,
        skill_service=ClawHubSkillService(
            config_dir=tmp_path / "config",
            on_skill_mutated=reload_recorder,
        ),
    )
    return memory_service, evolution_service, reload_recorder


class TestMemoryEvolutionRepository:
    async def test_schema_is_created(self, tmp_path: Path) -> None:
        repository = MemoryBankRepository(tmp_path / "schema.db")

        row = await repository._run_async_read(
            lambda conn: async_fetchone(
                conn,
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='memory_evolution_drafts'",
            )
        )

        assert row is not None


class TestMemoryEvolutionService:
    async def test_create_draft_from_active_memory(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)

        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SOP_SKILL,
                skill_id="review-loop-sop",
                runtime_name="review-loop-sop",
            )
        )

        assert draft.status == MemoryEvolutionStatus.DRAFT
        assert draft.source_memory_ids == (memory_id,)
        assert "## Procedure" in draft.instructions
        assert "## Source Memory" in draft.instructions

        result = await evolution_service.list_drafts_async(
            MemoryEvolutionDraftQuery(workspace_id="ws-evo")
        )
        assert result.total_count == 1
        assert result.items[0].draft_id == draft.draft_id

    async def test_create_rejects_cross_workspace_memory(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service, workspace_id="ws-other")

        with pytest.raises(ValueError, match="different workspace"):
            await evolution_service.create_draft_async(
                CreateMemoryEvolutionDraftRequest(
                    workspace_id="ws-evo",
                    source_memory_ids=(memory_id,),
                    target=MemoryEvolutionTarget.SKILL,
                    skill_id="bad-skill",
                    runtime_name="bad-skill",
                )
            )

    async def test_create_rejects_inactive_memory(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        await memory_service.update_entry_async(
            memory_id,
            UpdateMemoryEntryRequest(status=MemoryEntryStatus.EXPIRED),
        )

        with pytest.raises(ValueError, match="not active"):
            await evolution_service.create_draft_async(
                CreateMemoryEvolutionDraftRequest(
                    workspace_id="ws-evo",
                    source_memory_ids=(memory_id,),
                    target=MemoryEvolutionTarget.SKILL,
                    skill_id="expired-skill",
                    runtime_name="expired-skill",
                )
            )

    async def test_apply_draft_writes_skill_and_marks_source_memory(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, reload_recorder = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SOP_SKILL,
                skill_id="review-loop-sop",
                runtime_name="review-loop-sop",
            )
        )

        applied = await evolution_service.apply_draft_async(
            "ws-evo",
            draft.draft_id,
            ApplyMemoryEvolutionDraftRequest(),
        )

        assert applied is not None
        assert applied.status == MemoryEvolutionStatus.APPLIED
        assert applied.applied_skill_ref == "review-loop-sop"
        assert reload_recorder.count == 1
        skill_path = tmp_path / "config" / "skills" / "review-loop-sop" / "SKILL.md"
        assert skill_path.exists()
        assert "review-loop-sop" in skill_path.read_text(encoding="utf-8")

        source = await memory_service.get_entry_async(memory_id)
        assert source is not None
        assert source.metadata["evolution_draft_id"] == draft.draft_id
        assert source.metadata["evolution_skill_ref"] == "review-loop-sop"

    async def test_reject_draft_blocks_later_apply(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="rejected-skill",
                runtime_name="rejected-skill",
            )
        )

        rejected = await evolution_service.reject_draft_async(
            "ws-evo",
            draft.draft_id,
            RejectMemoryEvolutionDraftRequest(reason="Not useful enough"),
        )

        assert rejected is not None
        assert rejected.status == MemoryEvolutionStatus.REJECTED
        assert rejected.rejection_reason == "Not useful enough"
        with pytest.raises(ValueError, match="not applicable"):
            await evolution_service.apply_draft_async(
                "ws-evo",
                draft.draft_id,
                ApplyMemoryEvolutionDraftRequest(),
            )
