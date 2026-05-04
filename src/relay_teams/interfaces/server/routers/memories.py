# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response

from relay_teams.interfaces.server.deps import get_memory_bank_service
from relay_teams.memory.models import (
    CreateMemoryEntryRequest,
    MemoryConsolidationRequest,
    MemoryConsolidationResult,
    MemoryEntry,
    MemoryEntryKind,
    MemoryEntryStatus,
    MemoryQuery,
    MemoryQueryResult,
    MemoryScope,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryTier,
    UpdateMemoryEntryRequest,
)
from relay_teams.memory.service import MemoryBankService
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(tags=["Memories"])


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
    parsed_tags: tuple[str, ...] = ()
    if tags is not None and tags.strip():
        parsed_tags = tuple(t.strip() for t in tags.split(",") if t.strip())

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
