# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from agent_teams.interfaces.server.deps import get_reflection_service
from agent_teams.reflection import (
    DailyMemoryKind,
    ReflectionJobRecord,
    ReflectionService,
)

router = APIRouter(prefix="/reflection", tags=["Reflection"])


class MemoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    exists: bool
    content: str


@router.get("/jobs", response_model=list[ReflectionJobRecord])
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    service: ReflectionService = Depends(get_reflection_service),
) -> list[ReflectionJobRecord]:
    return list(service.list_jobs(limit=limit))


@router.post("/jobs/{job_id}/retry", response_model=ReflectionJobRecord)
def retry_job(
    job_id: str,
    service: ReflectionService = Depends(get_reflection_service),
) -> ReflectionJobRecord:
    try:
        return service.retry_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Reflection job not found") from exc


@router.get(
    "/memory/session-roles/{session_id}/{role_id}", response_model=MemoryResponse
)
def get_long_term_memory(
    session_id: str,
    role_id: str,
    service: ReflectionService = Depends(get_reflection_service),
) -> MemoryResponse:
    view = service.read_long_term_memory(session_id=session_id, role_id=role_id)
    return MemoryResponse(path=str(view.path), exists=view.exists, content=view.content)


@router.get(
    "/memory/instances/{instance_id}/daily/{memory_date}",
    response_model=MemoryResponse,
)
def get_daily_memory(
    instance_id: str,
    memory_date: str,
    kind: DailyMemoryKind = Query(default=DailyMemoryKind.DIGEST),
    service: ReflectionService = Depends(get_reflection_service),
) -> MemoryResponse:
    try:
        view = service.read_daily_memory(
            instance_id=instance_id,
            memory_date=memory_date,
            kind=kind,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent instance not found") from exc
    return MemoryResponse(path=str(view.path), exists=view.exists, content=view.content)
