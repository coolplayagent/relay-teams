# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from relay_teams.memory.evolution_service import (
    MemoryEvolutionConflictError,
    MemoryEvolutionService,
)
from relay_teams.memory.models import (
    ApplyMemoryEvolutionDraftRequest,
    CreateMemoryEntryRequest,
    CreateMemoryEvolutionDraftRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEvolutionDraft,
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
    body: str = "Capture useful review feedback as a reusable SOP.",
    kind: MemoryEntryKind = MemoryEntryKind.INSIGHT,
    context: str = "",
    outcome: str = "",
    metadata: dict[str, str] | None = None,
) -> str:
    entry = await service.create_entry_async(
        CreateMemoryEntryRequest(
            workspace_id=workspace_id,
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
            kind=kind,
            content=MemoryContent(
                title=title,
                body=body,
                context=context,
                outcome=outcome,
            ),
            source=MemorySourceKind.MANUAL,
            metadata=metadata or {},
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

    async def test_get_missing_draft_returns_none(self, tmp_path: Path) -> None:
        repository = MemoryBankRepository(tmp_path / "missing.db")

        draft = await repository.get_evolution_draft_async("mem-evo-missing")

        assert draft is None


class TestMemoryEvolutionModels:
    async def test_workspace_id_can_be_path_derived(self) -> None:
        request = CreateMemoryEvolutionDraftRequest(
            source_memory_ids=("mem-1",),
            skill_id="review-loop-sop",
            runtime_name="review-loop-sop",
        )

        assert request.workspace_id == ""

    async def test_source_memory_ids_reject_blank_values(self) -> None:
        with pytest.raises(ValidationError, match="non-empty"):
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(" ",),
                skill_id="review-loop-sop",
                runtime_name="review-loop-sop",
            )

    async def test_skill_ids_are_stripped_and_validated(self) -> None:
        request = CreateMemoryEvolutionDraftRequest(
            workspace_id="ws-evo",
            source_memory_ids=("mem-1",),
            skill_id=" review-loop-sop ",
            runtime_name=" review-loop-sop ",
        )

        assert request.skill_id == "review-loop-sop"
        assert request.runtime_name == "review-loop-sop"

        with pytest.raises(ValidationError, match="skill_id must start"):
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=("mem-1",),
                skill_id="bad/skill",
                runtime_name="review-loop-sop",
            )

        with pytest.raises(ValidationError, match="runtime_name must start"):
            ApplyMemoryEvolutionDraftRequest(runtime_name=" ")

    async def test_source_memory_ids_reject_duplicates_after_trim(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate source memory id"):
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=("mem-1", " mem-1 "),
                skill_id="review-loop-sop",
                runtime_name="review-loop-sop",
            )


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

    async def test_create_general_skill_includes_context_and_outcome(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(
            memory_service,
            title="Testing convention",
            body="Use focused unit coverage for changed behavior.",
            context="Memory Bank evolution PR",
            outcome="CI changed-line coverage stayed above the threshold.",
        )

        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="coverage-skill",
                runtime_name="coverage-skill",
                objective="Preserve changed-line coverage.",
            )
        )

        assert "## Operating Guidance" in draft.instructions
        assert "Memory Bank evolution PR" in draft.instructions
        assert "CI changed-line coverage" in draft.instructions

    async def test_create_sop_lists_failure_modes(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(
            memory_service,
            title="Coverage failure",
            body="Changed-line coverage can fail after broad API additions.",
            kind=MemoryEntryKind.FAILURE_MODE,
        )

        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SOP_SKILL,
                skill_id="coverage-failure-sop",
                runtime_name="coverage-failure-sop",
            )
        )

        assert "## Failure Modes" in draft.instructions
        assert "- Changed-line coverage can fail" in draft.instructions

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

    async def test_create_rejects_unknown_memory(self, tmp_path: Path) -> None:
        _, evolution_service, _ = _build_services(tmp_path)

        with pytest.raises(ValueError, match="Unknown source memory entry"):
            await evolution_service.create_draft_async(
                CreateMemoryEvolutionDraftRequest(
                    workspace_id="ws-evo",
                    source_memory_ids=("mem-missing",),
                    target=MemoryEvolutionTarget.SKILL,
                    skill_id="missing-skill",
                    runtime_name="missing-skill",
                )
            )

    async def test_create_requires_workspace_id_in_service(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)

        with pytest.raises(ValueError, match="workspace_id is required"):
            await evolution_service.create_draft_async(
                CreateMemoryEvolutionDraftRequest(
                    source_memory_ids=(memory_id,),
                    target=MemoryEvolutionTarget.SKILL,
                    skill_id="missing-workspace-skill",
                    runtime_name="missing-workspace-skill",
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

    async def test_list_drafts_filters_by_target_and_status(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        sop_draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SOP_SKILL,
                skill_id="filtered-sop",
                runtime_name="filtered-sop",
            )
        )
        skill_draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="filtered-skill",
                runtime_name="filtered-skill",
            )
        )
        await evolution_service.reject_draft_async(
            "ws-evo",
            skill_draft.draft_id,
            RejectMemoryEvolutionDraftRequest(reason="duplicate"),
        )

        result = await evolution_service.list_drafts_async(
            MemoryEvolutionDraftQuery(
                workspace_id="ws-evo",
                target=MemoryEvolutionTarget.SOP_SKILL,
                status=MemoryEvolutionStatus.DRAFT,
            )
        )

        assert result.total_count == 1
        assert result.items[0].draft_id == sop_draft.draft_id

    async def test_get_draft_returns_none_for_wrong_workspace(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="workspace-skill",
                runtime_name="workspace-skill",
            )
        )

        assert (
            await evolution_service.get_draft_async("ws-other", draft.draft_id) is None
        )

    async def test_apply_returns_none_for_missing_draft(self, tmp_path: Path) -> None:
        _, evolution_service, _ = _build_services(tmp_path)

        result = await evolution_service.apply_draft_async(
            "ws-evo",
            "mem-evo-missing",
            ApplyMemoryEvolutionDraftRequest(),
        )

        assert result is None

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

    async def test_apply_draft_can_default_description_and_trim_source_metadata(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        metadata = {f"k{index:02d}": f"v{index:02d}" for index in range(20)}
        memory_id = await _create_memory(
            memory_service,
            metadata=metadata,
            title="Metadata-heavy SOP",
        )
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SOP_SKILL,
                skill_id="metadata-sop",
                runtime_name="metadata-sop",
            )
        )

        applied = await evolution_service.apply_draft_async(
            "ws-evo",
            draft.draft_id,
            ApplyMemoryEvolutionDraftRequest(description=""),
        )

        assert applied is not None
        assert applied.description == (
            "SOP skill distilled from Memory Bank: Metadata-heavy SOP"
        )
        source = await memory_service.get_entry_async(memory_id)
        assert source is not None
        assert len(source.metadata) == 20
        assert source.metadata["evolution_draft_id"] == draft.draft_id
        assert source.metadata["evolution_skill_ref"] == "metadata-sop"

    async def test_apply_draft_allows_only_one_concurrent_claim(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, reload_recorder = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="concurrent-apply",
                runtime_name="concurrent-apply",
            )
        )

        results = await asyncio.gather(
            evolution_service.apply_draft_async(
                "ws-evo",
                draft.draft_id,
                ApplyMemoryEvolutionDraftRequest(),
            ),
            evolution_service.apply_draft_async(
                "ws-evo",
                draft.draft_id,
                ApplyMemoryEvolutionDraftRequest(
                    skill_id="concurrent-apply-other",
                    runtime_name="concurrent-apply-other",
                ),
            ),
            return_exceptions=True,
        )

        applied: list[MemoryEvolutionDraft] = []
        conflicts: list[MemoryEvolutionConflictError] = []
        for result in results:
            if isinstance(result, MemoryEvolutionConflictError):
                conflicts.append(result)
            elif isinstance(result, BaseException):
                raise result
            elif result is not None:
                applied.append(result)
        assert len(applied) == 1
        assert len(conflicts) == 1
        assert applied[0].status == MemoryEvolutionStatus.APPLIED
        assert reload_recorder.count == 1

    async def test_apply_and_reject_cannot_both_claim_draft(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, reload_recorder = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="apply-reject-race",
                runtime_name="apply-reject-race",
            )
        )

        results = await asyncio.gather(
            evolution_service.apply_draft_async(
                "ws-evo",
                draft.draft_id,
                ApplyMemoryEvolutionDraftRequest(),
            ),
            evolution_service.reject_draft_async(
                "ws-evo",
                draft.draft_id,
                RejectMemoryEvolutionDraftRequest(reason="not reusable"),
            ),
            return_exceptions=True,
        )

        completed: list[MemoryEvolutionDraft] = []
        conflicts: list[MemoryEvolutionConflictError] = []
        for result in results:
            if isinstance(result, MemoryEvolutionConflictError):
                conflicts.append(result)
            elif isinstance(result, BaseException):
                raise result
            elif result is not None:
                completed.append(result)
        assert len(completed) == 1
        assert len(conflicts) == 1
        assert completed[0].status in {
            MemoryEvolutionStatus.APPLIED,
            MemoryEvolutionStatus.REJECTED,
        }
        assert reload_recorder.count in {0, 1}
        if completed[0].status == MemoryEvolutionStatus.REJECTED:
            assert reload_recorder.count == 0
        else:
            assert reload_recorder.count == 1

    async def test_apply_draft_rejects_blank_instructions(self, tmp_path: Path) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="blank-instructions",
                runtime_name="blank-instructions",
            )
        )

        with pytest.raises(ValueError, match="instructions must be non-empty"):
            await evolution_service.apply_draft_async(
                "ws-evo",
                draft.draft_id,
                ApplyMemoryEvolutionDraftRequest(instructions=" "),
            )

    async def test_reject_returns_none_for_missing_draft(self, tmp_path: Path) -> None:
        _, evolution_service, _ = _build_services(tmp_path)

        result = await evolution_service.reject_draft_async(
            "ws-evo",
            "mem-evo-missing",
            RejectMemoryEvolutionDraftRequest(reason="missing"),
        )

        assert result is None

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

    async def test_reject_draft_blocks_already_applied_draft(
        self, tmp_path: Path
    ) -> None:
        memory_service, evolution_service, _ = _build_services(tmp_path)
        memory_id = await _create_memory(memory_service)
        draft = await evolution_service.create_draft_async(
            CreateMemoryEvolutionDraftRequest(
                workspace_id="ws-evo",
                source_memory_ids=(memory_id,),
                target=MemoryEvolutionTarget.SKILL,
                skill_id="applied-skill",
                runtime_name="applied-skill",
            )
        )
        await evolution_service.apply_draft_async(
            "ws-evo",
            draft.draft_id,
            ApplyMemoryEvolutionDraftRequest(),
        )

        with pytest.raises(ValueError, match="not rejectable"):
            await evolution_service.reject_draft_async(
                "ws-evo",
                draft.draft_id,
                RejectMemoryEvolutionDraftRequest(reason="too late"),
            )
