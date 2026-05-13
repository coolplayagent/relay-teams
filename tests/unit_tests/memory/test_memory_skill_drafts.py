# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import override

import pytest

from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryContent,
    MemoryEntryKind,
    MemoryScope,
    MemorySourceKind,
    MemoryTier,
)
from relay_teams.memory.repository import MemoryBankRepository
from relay_teams.memory.service import MemoryBankService
from relay_teams.memory.skill_draft_models import (
    GenerateMemorySkillDraftsRequest,
    MemorySkillDraft,
    MemorySkillDraftFile,
    MemorySkillDraftGenerationKind,
    MemorySkillDraftKind,
    MemorySkillDraftQuery,
    MemorySkillDraftScopeKind,
    MemorySkillDraftStatus,
    MemorySkillDraftValidationMessage,
    MemorySkillDraftValidationSeverity,
    UpdateMemorySkillDraftRequest,
)
from relay_teams.memory.skill_draft_repository import MemorySkillDraftRepository
from relay_teams.memory.skill_draft_validator import SkillDraftValidator
from relay_teams.memory.skill_synthesis_service import (
    MemorySkillSynthesisService,
    _GeneratedMemorySkillDraft,
    _build_generation_prompt,
    _build_integrated_candidate,
    _format_memory_for_prompt,
    _looks_like_one_draft_per_memory,
    _normalize_skill_name,
    _resolve_candidate_kind,
    _strip_json_code_fences,
)
from relay_teams.providers.provider_contracts import LLMProvider, LLMRequest
from relay_teams.skills.clawhub_models import (
    ClawHubSkillDetail,
    ClawHubSkillWriteRequest,
)
from relay_teams.skills.clawhub_skill_service import ClawHubSkillService

pytestmark = pytest.mark.asyncio


class _JsonProvider(LLMProvider):
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    @override
    async def generate(self, _request: LLMRequest) -> str:
        return json.dumps(self._payload)


def _build_services(
    tmp_path: Path,
    provider: LLMProvider | None,
) -> tuple[MemoryBankService, MemorySkillDraftRepository, MemorySkillSynthesisService]:
    db_path = tmp_path / "memory.db"
    memory_service = MemoryBankService(repository=MemoryBankRepository(db_path))
    draft_repo = MemorySkillDraftRepository(db_path)
    clawhub_service = ClawHubSkillService(config_dir=tmp_path / "config")
    synthesis_service = MemorySkillSynthesisService(
        draft_repository=draft_repo,
        memory_bank_service=memory_service,
        clawhub_skill_service=clawhub_service,
        llm_provider_resolver=lambda: provider,
    )
    return memory_service, draft_repo, synthesis_service


async def _create_memory(
    service: MemoryBankService,
    *,
    workspace_id: str,
    title: str,
    body: str,
) -> str:
    entry = await service.create_entry_async(
        CreateMemoryEntryRequest(
            tier=MemoryTier.PERSISTENT,
            scope=MemoryScope.WORKSPACE,
            workspace_id=workspace_id,
            kind=MemoryEntryKind.FACT,
            content=MemoryContent(title=title, body=body),
            source=MemorySourceKind.MANUAL,
        )
    )
    return entry.id


async def test_generation_merges_one_draft_per_memory_output(tmp_path: Path) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "skill",
                    "runtime_name": "first-memory",
                    "description": "First memory",
                    "instructions": "Use the first memory.",
                    "source_memory_ids": ["mem-a"],
                },
                {
                    "draft_kind": "skill",
                    "runtime_name": "second-memory",
                    "description": "Second memory",
                    "instructions": "Use the second memory.",
                    "source_memory_ids": ["mem-b"],
                },
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Use Pydantic models",
        body="Domain contracts should use explicit Pydantic v2 models.",
    )
    await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Use pathlib",
        body="Use pathlib.Path for filesystem paths.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
            workspace_id="ws-1",
        )
    )

    assert result.source_memory_count == 2
    assert len(result.items) == 1
    assert result.items[0].source_memory_count == 2


async def test_cross_workspace_generation_uses_multiple_workspaces(
    tmp_path: Path,
) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "sop_skill",
                    "runtime_name": "memory-review-sop",
                    "description": "Apply repeated review memory.",
                    "instructions": "# Review SOP\n\nUse all captured review steps.",
                    "source_memory_ids": [],
                }
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Review API docs",
        body="Update API docs with backend changes.",
    )
    await _create_memory(
        memory_service,
        workspace_id="ws-2",
        title="Run validation",
        body="Run unit tests after contract changes.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
            workspace_ids=("ws-1", "ws-2"),
        )
    )
    draft = await synthesis.get_draft_async(result.items[0].id)

    assert draft is not None
    assert draft.scope_kind == MemorySkillDraftScopeKind.CROSS_WORKSPACE
    assert draft.workspace_ids == ("ws-1", "ws-2")
    assert len(draft.source_memory_ids) == 2


async def test_blank_workspace_id_is_rejected_for_workspace_generation(
    tmp_path: Path,
) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "skill",
                    "runtime_name": "workspace-memory",
                    "description": "Use workspace memory.",
                    "instructions": "Use captured memory.",
                    "source_memory_ids": [],
                }
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Workspace memory",
        body="This must not be loaded by a blank workspace request.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
            workspace_id=" ",
        )
    )

    assert result.items == ()
    assert result.error_message == "workspace_id is required for workspace skill drafts"


async def test_workspace_generation_rejects_workspace_ids_without_workspace_id(
    tmp_path: Path,
) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "skill",
                    "runtime_name": "workspace-memory",
                    "description": "Use workspace memory.",
                    "instructions": "Use captured memory.",
                    "source_memory_ids": [],
                }
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Workspace memory",
        body="This must not be loaded through workspace_ids fallback.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
            workspace_ids=("ws-1",),
        )
    )

    assert result.items == ()
    assert result.error_message == "workspace_id is required for workspace skill drafts"


async def test_cross_workspace_text_search_filters_each_workspace_before_limit(
    tmp_path: Path,
) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "skill",
                    "runtime_name": "workspace-memory",
                    "description": "Use workspace memory.",
                    "instructions": "Use captured memory.",
                    "source_memory_ids": [],
                }
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    ws_1_memory = await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Shared review practice",
        body="Shared search target for workspace one.",
    )
    await _create_memory(
        memory_service,
        workspace_id="ws-noise",
        title="Shared review practice",
        body="Shared search target outside the requested workspaces.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
            workspace_ids=("ws-1",),
            text_query="Shared",
            limit=1,
        )
    )
    draft = await synthesis.get_draft_async(result.items[0].id)

    assert draft is not None
    assert draft.source_memory_ids == (ws_1_memory,)


async def test_validate_and_apply_skill_draft(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    draft = MemorySkillDraft(
        id="msd-test",
        status=MemorySkillDraftStatus.DRAFT,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1", "mem-2"),
        draft_kind=MemorySkillDraftKind.SOP_SKILL,
        runtime_name="workspace-sop",
        description="Use the workspace SOP.",
        instructions="# SOP\n\nFollow the repeated workspace procedure.",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    await draft_repo.create_draft_async(draft)

    validated = await synthesis.validate_draft_async("msd-test")
    assert validated is not None
    assert validated.status == MemorySkillDraftStatus.VALIDATED

    result = await synthesis.apply_draft_async("msd-test")
    assert result.ref == "workspace-sop"
    assert (tmp_path / "config" / "skills" / "workspace-sop" / "SKILL.md").exists()

    with pytest.raises(ValueError, match="Only validated skill drafts can be applied"):
        await synthesis.apply_draft_async("msd-test")


async def test_apply_keeps_claim_when_skill_write_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    draft = MemorySkillDraft(
        id="msd-cancel-apply",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
        validated_at=datetime.now(tz=timezone.utc),
    )
    await draft_repo.create_draft_async(draft)

    def cancel_save(
        _skill_id: str, _request: ClawHubSkillWriteRequest
    ) -> ClawHubSkillDetail:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        synthesis._clawhub_skill_service,
        "save_skill",
        cancel_save,
    )

    with pytest.raises(asyncio.CancelledError):
        await synthesis.apply_draft_async("msd-cancel-apply")

    stored = await synthesis.get_draft_async("msd-cancel-apply")
    assert stored is not None
    assert stored.status == MemorySkillDraftStatus.APPLYING


async def test_apply_keeps_claim_when_skill_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    draft = MemorySkillDraft(
        id="msd-fail-apply",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
        validated_at=datetime.now(tz=timezone.utc),
    )
    await draft_repo.create_draft_async(draft)

    def fail_save(
        _skill_id: str, _request: ClawHubSkillWriteRequest
    ) -> ClawHubSkillDetail:
        raise RuntimeError("post-write hook failed")

    monkeypatch.setattr(
        synthesis._clawhub_skill_service,
        "save_skill",
        fail_save,
    )

    with pytest.raises(RuntimeError, match="post-write hook failed"):
        await synthesis.apply_draft_async("msd-fail-apply")

    stored = await synthesis.get_draft_async("msd-fail-apply")
    assert stored is not None
    assert stored.status == MemorySkillDraftStatus.APPLYING


async def test_apply_claim_is_atomic(tmp_path: Path) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    draft = MemorySkillDraft(
        id="msd-claim",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    await draft_repo.create_draft_async(draft)

    first_claim = await draft_repo.claim_draft_apply_async(
        draft_id="msd-claim",
        updated_at=datetime.now(tz=timezone.utc),
    )
    second_claim = await draft_repo.claim_draft_apply_async(
        draft_id="msd-claim",
        updated_at=datetime.now(tz=timezone.utc),
    )

    assert first_claim is not None
    assert first_claim.status == MemorySkillDraftStatus.APPLYING
    assert second_claim is None


async def test_repository_apply_claim_release_and_complete_paths(
    tmp_path: Path,
) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-state",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    assert await draft_repo.get_draft_async("missing") is None
    claimed = await draft_repo.claim_draft_apply_async(
        draft_id="msd-state",
        updated_at=now,
    )
    assert claimed is not None
    assert claimed.status == MemorySkillDraftStatus.APPLYING
    assert await draft_repo.release_draft_apply_claim_async(
        draft_id="msd-state",
        updated_at=now,
    )
    assert not await draft_repo.release_draft_apply_claim_async(
        draft_id="msd-state",
        updated_at=now,
    )

    claimed_again = await draft_repo.claim_draft_apply_async(
        draft_id="msd-state",
        updated_at=now,
    )
    assert claimed_again is not None
    applied = claimed_again.model_copy(
        update={
            "status": MemorySkillDraftStatus.APPLIED,
            "applied_skill_id": "workspace-memory",
            "applied_ref": "workspace-memory",
            "applied_at": now,
            "updated_at": now,
        }
    )
    completed = await draft_repo.complete_draft_apply_async(draft=applied)
    assert completed is not None
    assert completed.status == MemorySkillDraftStatus.APPLIED

    repeated = await draft_repo.complete_draft_apply_async(draft=applied)
    assert repeated is not None
    assert repeated.applied_ref == "workspace-memory"


async def test_repository_update_can_require_expected_status(tmp_path: Path) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-expected-status",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
        validated_at=now,
    )
    await draft_repo.create_draft_async(draft)
    claimed = await draft_repo.claim_draft_apply_async(
        draft_id="msd-expected-status",
        updated_at=now,
    )
    assert claimed is not None

    stale_update = await draft_repo.update_draft_async(
        draft.model_copy(update={"description": "Stale edit."}),
        expected_status=MemorySkillDraftStatus.VALIDATED,
    )
    stored = await draft_repo.get_draft_async("msd-expected-status")

    assert stale_update is None
    assert stored is not None
    assert stored.status == MemorySkillDraftStatus.APPLYING
    assert stored.description == "Use workspace memory."


async def test_repository_complete_apply_rejects_non_applying_draft(
    tmp_path: Path,
) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-not-applying",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    result = await draft_repo.complete_draft_apply_async(
        draft=draft.model_copy(
            update={
                "status": MemorySkillDraftStatus.APPLIED,
                "applied_skill_id": "workspace-memory",
                "applied_ref": "workspace-memory",
                "applied_at": now,
            }
        )
    )

    assert result is None


async def test_validate_applied_draft_does_not_regress_status(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-applied",
        status=MemorySkillDraftStatus.APPLIED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        applied_ref="workspace-memory",
        created_at=now,
        updated_at=now,
        applied_at=now,
    )
    await draft_repo.create_draft_async(draft)

    validated = await synthesis.validate_draft_async("msd-applied")

    assert validated is not None
    assert validated.status == MemorySkillDraftStatus.APPLIED
    assert validated.applied_ref == "workspace-memory"


async def test_validate_applying_draft_is_rejected(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-applying",
        status=MemorySkillDraftStatus.APPLYING,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    with pytest.raises(ValueError, match="Applying skill drafts cannot be validated"):
        await synthesis.validate_draft_async("msd-applying")

    stored = await synthesis.get_draft_async("msd-applying")
    assert stored is not None
    assert stored.status == MemorySkillDraftStatus.APPLYING


async def test_update_rejected_content_resets_to_draft(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-rejected",
        status=MemorySkillDraftStatus.REJECTED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    updated = await synthesis.update_draft_async(
        "msd-rejected",
        UpdateMemorySkillDraftRequest(instructions="Use updated memory."),
    )

    assert updated is not None
    assert updated.status == MemorySkillDraftStatus.DRAFT
    assert updated.validation_messages == ()


async def test_update_rejects_blank_runtime_name(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    draft = MemorySkillDraft(
        id="msd-blank",
        status=MemorySkillDraftStatus.DRAFT,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    await draft_repo.create_draft_async(draft)

    with pytest.raises(ValueError, match="runtime_name must be non-empty"):
        await synthesis.update_draft_async(
            "msd-blank",
            UpdateMemorySkillDraftRequest(runtime_name="   "),
        )


async def test_update_noop_preserves_validated_status(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-noop",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
        validated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    updated = await synthesis.update_draft_async(
        "msd-noop",
        UpdateMemorySkillDraftRequest(
            runtime_name="workspace-memory",
            description="Use workspace memory.",
            instructions="Use the workspace memory.",
        ),
    )

    assert updated is not None
    assert updated.status == MemorySkillDraftStatus.VALIDATED
    assert updated.validated_at == now


async def test_update_rejects_applied_draft_mutation(tmp_path: Path) -> None:
    _, draft_repo, synthesis = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-applied-edit",
        status=MemorySkillDraftStatus.APPLIED,
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        workspace_ids=("ws-1",),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="workspace-memory",
        description="Use workspace memory.",
        instructions="Use the workspace memory.",
        created_at=now,
        updated_at=now,
        applied_at=now,
    )
    await draft_repo.create_draft_async(draft)

    with pytest.raises(ValueError, match="Applied skill drafts cannot be edited"):
        await synthesis.update_draft_async(
            "msd-applied-edit",
            UpdateMemorySkillDraftRequest(instructions="Edited after apply."),
        )
    with pytest.raises(ValueError, match="Applied skill drafts cannot be edited"):
        await synthesis.update_draft_async(
            "msd-applied-edit",
            UpdateMemorySkillDraftRequest(runtime_name="workspace-memory"),
        )


async def test_workspace_query_escapes_json_like_wildcards(tmp_path: Path) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    await draft_repo.create_draft_async(
        MemorySkillDraft(
            id="msd-wildcard-exact",
            status=MemorySkillDraftStatus.DRAFT,
            scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
            workspace_ids=("ws_1",),
            source_memory_ids=("mem-1",),
            draft_kind=MemorySkillDraftKind.SKILL,
            runtime_name="workspace-exact",
            description="Use exact workspace memory.",
            instructions="Use exact workspace memory.",
            created_at=now,
            updated_at=now,
        )
    )
    await draft_repo.create_draft_async(
        MemorySkillDraft(
            id="msd-wildcard-noise",
            status=MemorySkillDraftStatus.DRAFT,
            scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
            workspace_ids=("wsA1",),
            source_memory_ids=("mem-2",),
            draft_kind=MemorySkillDraftKind.SKILL,
            runtime_name="workspace-noise",
            description="Use another workspace memory.",
            instructions="Use another workspace memory.",
            created_at=now,
            updated_at=now,
        )
    )

    result = await draft_repo.query_drafts_async(
        MemorySkillDraftQuery(workspace_id="ws_1")
    )

    assert tuple(item.id for item in result.items) == ("msd-wildcard-exact",)


async def test_repository_query_filters_and_loads_files_and_messages(
    tmp_path: Path,
) -> None:
    _, draft_repo, _ = _build_services(tmp_path, provider=None)
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-filtered",
        status=MemorySkillDraftStatus.VALIDATED,
        scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
        workspace_ids=("ws-1", "ws-2"),
        source_memory_ids=("mem-1",),
        draft_kind=MemorySkillDraftKind.SOP_SKILL,
        runtime_name="workspace-memory-sop",
        description="Needle guidance.",
        instructions="Use the needle guidance.",
        files=(MemorySkillDraftFile(path="scripts/apply.py", content="print('ok')"),),
        validation_messages=(
            MemorySkillDraftValidationMessage(
                severity=MemorySkillDraftValidationSeverity.WARNING,
                code="long_instructions",
                message="instructions are long",
                path="SKILL.md",
            ),
        ),
        created_at=now,
        updated_at=now,
        validated_at=now,
    )
    await draft_repo.create_draft_async(draft)

    result = await draft_repo.query_drafts_async(
        MemorySkillDraftQuery(
            scope_kind=MemorySkillDraftScopeKind.CROSS_WORKSPACE,
            workspace_id="ws-1",
            status=MemorySkillDraftStatus.VALIDATED,
            draft_kind=MemorySkillDraftKind.SOP_SKILL,
            text_query="Needle",
        )
    )
    loaded = await draft_repo.get_draft_async("msd-filtered")

    assert tuple(item.id for item in result.items) == ("msd-filtered",)
    assert loaded is not None
    assert loaded.files[0].path == "scripts/apply.py"
    assert loaded.validation_messages[0].code == "long_instructions"


async def test_explicit_source_ids_must_match_workspace_scope(
    tmp_path: Path,
) -> None:
    provider = _JsonProvider(
        {
            "drafts": [
                {
                    "draft_kind": "skill",
                    "runtime_name": "workspace-memory",
                    "description": "Use workspace memory.",
                    "instructions": "Use captured memory.",
                    "source_memory_ids": [],
                }
            ]
        }
    )
    memory_service, _, synthesis = _build_services(tmp_path, provider)
    ws_1_memory = await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Workspace one",
        body="Use workspace one rules.",
    )
    ws_2_memory = await _create_memory(
        memory_service,
        workspace_id="ws-2",
        title="Workspace two",
        body="Use workspace two rules.",
    )

    result = await synthesis.generate_drafts_async(
        GenerateMemorySkillDraftsRequest(
            scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
            workspace_id="ws-1",
            source_memory_ids=(ws_1_memory, ws_2_memory),
        )
    )

    assert result.items == ()
    assert "workspace scope" in result.error_message


async def test_validator_rejects_invalid_skill_creator_shape() -> None:
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-invalid",
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="Bad_Name",
        description="",
        instructions="",
        files=(MemorySkillDraftFile(path="README.md", content="extra"),),
        created_at=now,
        updated_at=now,
    )

    validated = SkillDraftValidator().validate(draft)

    codes = {message.code for message in validated.validation_messages}
    assert "invalid_runtime_name" in codes
    assert "missing_description" in codes
    assert "missing_instructions" in codes
    assert "extraneous_doc" in codes


async def test_validator_reports_shape_edge_cases() -> None:
    now = datetime.now(tz=timezone.utc)
    draft = MemorySkillDraft(
        id="msd-invalid-shape",
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="valid-name",
        description="line one\nline two",
        instructions="x" * 20001,
        files=(
            MemorySkillDraftFile(path="../escape.txt", content="bad"),
            MemorySkillDraftFile(path="SKILL.md", content="managed"),
            MemorySkillDraftFile(path="README.md", content="extra"),
            MemorySkillDraftFile(path="data.txt", content="one"),
            MemorySkillDraftFile(path="data.txt", content="two"),
        ),
        created_at=now,
        updated_at=now,
    )

    validated = SkillDraftValidator().validate(draft)
    codes = {message.code for message in validated.validation_messages}

    assert "multiline_description" in codes
    assert "long_instructions" in codes
    assert "invalid_file_path" in codes
    assert "skill_md_managed" in codes
    assert "extraneous_doc" in codes
    assert "duplicate_file_path" in codes


async def test_synthesis_prompt_and_helper_branches(
    tmp_path: Path,
) -> None:
    memory_service, _, _ = _build_services(tmp_path, provider=None)
    memory_id = await _create_memory(
        memory_service,
        workspace_id="ws-1",
        title="Long practice",
        body="x" * 1500,
    )
    entry = await memory_service.get_entry_async(memory_id)
    assert entry is not None
    request = GenerateMemorySkillDraftsRequest(
        scope_kind=MemorySkillDraftScopeKind.WORKSPACE,
        workspace_id="ws-1",
        draft_kind=MemorySkillDraftGenerationKind.SOP_SKILL,
    )
    candidate = _GeneratedMemorySkillDraft(
        draft_kind=MemorySkillDraftKind.SKILL,
        runtime_name="Candidate Skill",
        description="Use candidate memory.",
        instructions="Use the candidate memory.",
        source_memory_ids=(memory_id,),
    )

    formatted = _format_memory_for_prompt(entry)
    prompt = _build_generation_prompt(request, (entry,))
    integrated = _build_integrated_candidate(
        request=request,
        source_entries=(entry,),
    )

    assert formatted.endswith("Outcome: ")
    assert "..." in formatted
    assert "Do not create one skill per memory entry" in prompt
    assert _strip_json_code_fences('```json\n{"drafts": []}\n```') == '{"drafts": []}'
    assert not _looks_like_one_draft_per_memory((candidate,), 1)
    assert not _looks_like_one_draft_per_memory((candidate,), 2)
    assert integrated.draft_kind == MemorySkillDraftKind.SOP_SKILL
    assert integrated.runtime_name.endswith("-sop")
    assert (
        _resolve_candidate_kind(
            MemorySkillDraftKind.SOP_SKILL,
            MemorySkillDraftGenerationKind.SKILL,
        )
        == MemorySkillDraftKind.SKILL
    )
    assert (
        _resolve_candidate_kind(
            MemorySkillDraftKind.SKILL,
            MemorySkillDraftGenerationKind.SOP_SKILL,
        )
        == MemorySkillDraftKind.SOP_SKILL
    )
    assert _normalize_skill_name("", MemorySkillDraftKind.SKILL) == "memory-skill"
    assert _normalize_skill_name("A" * 80, MemorySkillDraftKind.SOP_SKILL).endswith(
        "-sop"
    )
