# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response

from relay_teams.interfaces.server.deps import (
    get_memory_bank_service,
    get_memory_evolution_service,
    get_memory_skill_synthesis_service,
)
from relay_teams.memory.models import (
    ApplyMemoryEvolutionDraftRequest,
    CreateMemoryEntryRequest,
    CreateMemoryEvolutionDraftRequest,
    GlobalMemorySearchRequest,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryEvolutionDraft,
    MemoryEvolutionDraftQuery,
    MemoryEvolutionDraftQueryResult,
    MemoryEvolutionStatus,
    MemoryEvolutionTarget,
    MemoryQuery,
    MemoryQueryResult,
    MemoryScope,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryTier,
    RejectMemoryEvolutionDraftRequest,
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.evolution_service import (
    MemoryEvolutionConflictError,
    MemoryEvolutionService,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.memory.skill_draft_models import (
    GenerateMemorySkillDraftsRequest,
    MemorySkillDraft,
    MemorySkillDraftApplyResult,
    MemorySkillDraftGenerationResult,
    MemorySkillDraftKind,
    MemorySkillDraftQuery,
    MemorySkillDraftQueryResult,
    MemorySkillDraftScopeKind,
    MemorySkillDraftStatus,
    UpdateMemorySkillDraftRequest,
)
from relay_teams.memory.skill_synthesis_service import MemorySkillSynthesisService
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(tags=["Memories"])


@router.get(
    "/memories",
    response_model=MemoryQueryResult,
)
async def list_all_memories(
    workspace_id: str | None = Query(default=None),
    tier: MemoryTier | None = Query(default=None),
    scope: MemoryScope | None = Query(default=None),
    session_id: str | None = Query(default=None),
    role_id: str | None = Query(default=None),
    kind: MemoryEntryKind | None = Query(default=None),
    status: MemoryEntryStatus | None = Query(default=None),
    tags: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryQueryResult:
    parsed_tags = _parse_tags(tags)
    query = MemoryQuery(
        workspace_id=workspace_id,
        tier=tier,
        scope=scope,
        session_id=session_id,
        role_id=role_id,
        kind=kind,
        status=status,
        tags=parsed_tags,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    return await service.list_entries_async(query)


@router.post(
    "/memories/search",
    response_model=MemorySearchResult,
)
async def search_all_memories(
    body: GlobalMemorySearchRequest = Body(...),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemorySearchResult:
    return await service.search_global_async(body)


@router.post(
    "/memories/skill-drafts:generate",
    response_model=MemorySkillDraftGenerationResult,
    status_code=201,
)
async def generate_memory_skill_drafts(
    body: GenerateMemorySkillDraftsRequest = Body(...),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraftGenerationResult:
    return await service.generate_drafts_async(body)


@router.get(
    "/memories/skill-drafts",
    response_model=MemorySkillDraftQueryResult,
)
async def list_memory_skill_drafts(
    scope_kind: MemorySkillDraftScopeKind | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    status: MemorySkillDraftStatus | None = Query(default=None),
    draft_kind: MemorySkillDraftKind | None = Query(default=None),
    text_query: str = Query(default=""),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraftQueryResult:
    return await service.list_drafts_async(
        MemorySkillDraftQuery(
            scope_kind=scope_kind,
            workspace_id=workspace_id,
            status=status,
            draft_kind=draft_kind,
            text_query=text_query,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/memories/skill-drafts/{draft_id}",
    response_model=MemorySkillDraft,
)
async def get_memory_skill_draft(
    draft_id: RequiredIdentifierStr = Path(),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraft:
    draft = await service.get_draft_async(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory skill draft not found")
    return draft


@router.put(
    "/memories/skill-drafts/{draft_id}",
    response_model=MemorySkillDraft,
)
async def update_memory_skill_draft(
    draft_id: RequiredIdentifierStr = Path(),
    body: UpdateMemorySkillDraftRequest = Body(...),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraft:
    try:
        draft = await service.update_draft_async(draft_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory skill draft not found")
    return draft


@router.post(
    "/memories/skill-drafts/{draft_id}:validate",
    response_model=MemorySkillDraft,
)
async def validate_memory_skill_draft(
    draft_id: RequiredIdentifierStr = Path(),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraft:
    try:
        draft = await service.validate_draft_async(draft_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory skill draft not found")
    return draft


@router.post(
    "/memories/skill-drafts/{draft_id}:apply",
    response_model=MemorySkillDraftApplyResult,
)
async def apply_memory_skill_draft(
    draft_id: RequiredIdentifierStr = Path(),
    service: MemorySkillSynthesisService = Depends(get_memory_skill_synthesis_service),
) -> MemorySkillDraftApplyResult:
    try:
        return await service.apply_draft_async(draft_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail="Memory skill draft not found"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/workspaces/{workspace_id}/memories",
    response_model=MemoryQueryResult,
)
async def list_memories(
    workspace_id: RequiredIdentifierStr = Path(),
    tier: MemoryTier | None = Query(default=None),
    scope: MemoryScope | None = Query(default=None),
    session_id: str | None = Query(default=None),
    role_id: str | None = Query(default=None),
    kind: MemoryEntryKind | None = Query(default=None),
    status: MemoryEntryStatus | None = Query(default=None),
    tags: str | None = Query(default=None),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryQueryResult:
    parsed_tags = _parse_tags(tags)

    query = MemoryQuery(
        workspace_id=workspace_id,
        tier=tier,
        scope=scope,
        session_id=session_id,
        role_id=role_id,
        kind=kind,
        status=status,
        tags=parsed_tags,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    return await service.list_entries_async(query)


@router.post(
    "/workspaces/{workspace_id}/memories",
    response_model=MemoryEntry,
    status_code=201,
)
async def create_memory(
    workspace_id: RequiredIdentifierStr = Path(),
    body: CreateMemoryEntryRequest = Body(...),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryEntry:
    patched = body.model_copy(update={"workspace_id": workspace_id})
    return await service.create_entry_async(patched)


@router.post(
    "/workspaces/{workspace_id}/memories/evolutions",
    response_model=MemoryEvolutionDraft,
    status_code=201,
)
async def create_memory_evolution_draft(
    workspace_id: RequiredIdentifierStr = Path(),
    body: CreateMemoryEvolutionDraftRequest = Body(...),
    service: MemoryEvolutionService = Depends(get_memory_evolution_service),
) -> MemoryEvolutionDraft:
    patched = body.model_copy(update={"workspace_id": workspace_id})
    try:
        return await service.create_draft_async(patched)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/workspaces/{workspace_id}/memories/evolutions",
    response_model=MemoryEvolutionDraftQueryResult,
)
async def list_memory_evolution_drafts(
    workspace_id: RequiredIdentifierStr = Path(),
    target: MemoryEvolutionTarget | None = Query(default=None),
    status: MemoryEvolutionStatus | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    service: MemoryEvolutionService = Depends(get_memory_evolution_service),
) -> MemoryEvolutionDraftQueryResult:
    return await service.list_drafts_async(
        MemoryEvolutionDraftQuery(
            workspace_id=workspace_id,
            target=target,
            status=status,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/workspaces/{workspace_id}/memories/evolutions/{draft_id}",
    response_model=MemoryEvolutionDraft,
)
async def get_memory_evolution_draft(
    workspace_id: RequiredIdentifierStr = Path(),
    draft_id: RequiredIdentifierStr = Path(),
    service: MemoryEvolutionService = Depends(get_memory_evolution_service),
) -> MemoryEvolutionDraft:
    draft = await service.get_draft_async(workspace_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory evolution draft not found")
    return draft


@router.post(
    "/workspaces/{workspace_id}/memories/evolutions/{draft_id}:apply",
    response_model=MemoryEvolutionDraft,
)
async def apply_memory_evolution_draft(
    workspace_id: RequiredIdentifierStr = Path(),
    draft_id: RequiredIdentifierStr = Path(),
    body: ApplyMemoryEvolutionDraftRequest | None = Body(default=None),
    service: MemoryEvolutionService = Depends(get_memory_evolution_service),
) -> MemoryEvolutionDraft:
    payload = body or ApplyMemoryEvolutionDraftRequest()
    try:
        draft = await service.apply_draft_async(workspace_id, draft_id, payload)
    except MemoryEvolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory evolution draft not found")
    return draft


@router.post(
    "/workspaces/{workspace_id}/memories/evolutions/{draft_id}:reject",
    response_model=MemoryEvolutionDraft,
)
async def reject_memory_evolution_draft(
    workspace_id: RequiredIdentifierStr = Path(),
    draft_id: RequiredIdentifierStr = Path(),
    body: RejectMemoryEvolutionDraftRequest | None = Body(default=None),
    service: MemoryEvolutionService = Depends(get_memory_evolution_service),
) -> MemoryEvolutionDraft:
    payload = body or RejectMemoryEvolutionDraftRequest()
    try:
        draft = await service.reject_draft_async(workspace_id, draft_id, payload)
    except MemoryEvolutionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if draft is None:
        raise HTTPException(status_code=404, detail="Memory evolution draft not found")
    return draft


@router.get(
    "/workspaces/{workspace_id}/memories/{memory_id}",
    response_model=MemoryEntry,
)
async def get_memory(
    workspace_id: RequiredIdentifierStr = Path(),
    memory_id: RequiredIdentifierStr = Path(),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryEntry:
    entry = await service.get_entry_async(memory_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    if entry.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return entry


@router.put(
    "/workspaces/{workspace_id}/memories/{memory_id}",
    response_model=MemoryEntry,
)
async def update_memory(
    workspace_id: RequiredIdentifierStr = Path(),
    memory_id: RequiredIdentifierStr = Path(),
    body: UpdateMemoryEntryRequest = Body(...),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryEntry:
    existing = await service.get_entry_async(memory_id)
    if existing is None or existing.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    updated = await service.update_entry_async(memory_id, body)
    if updated is None:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    return updated


@router.delete(
    "/workspaces/{workspace_id}/memories/{memory_id}",
    status_code=204,
)
async def delete_memory(
    workspace_id: RequiredIdentifierStr = Path(),
    memory_id: RequiredIdentifierStr = Path(),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> Response:
    existing = await service.get_entry_async(memory_id)
    if existing is None or existing.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Memory entry not found")
    await service.delete_entry_async(memory_id)
    return Response(status_code=204)


@router.post(
    "/workspaces/{workspace_id}/memories/consolidate",
    response_model=MemoryConsolidationResult,
)
async def consolidate_memories(
    workspace_id: RequiredIdentifierStr = Path(),
    body: MemoryConsolidationRequest = Body(...),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemoryConsolidationResult:
    patched = body.model_copy(update={"workspace_id": workspace_id})
    return await service.consolidate_async(patched)


@router.post(
    "/workspaces/{workspace_id}/memories/search",
    response_model=MemorySearchResult,
)
async def search_memories(
    workspace_id: RequiredIdentifierStr = Path(),
    body: MemorySearchRequest = Body(...),
    service: MemoryBankService = Depends(get_memory_bank_service),
) -> MemorySearchResult:
    patched = body.model_copy(update={"workspace_id": workspace_id})
    return await service.search_async(patched)


def _parse_tags(tags: str | None) -> tuple[str, ...]:
    if tags is None or not tags.strip():
        return ()
    return tuple(t.strip() for t in tags.split(",") if t.strip())
