# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from relay_teams.agents.tasks.artifact_query_service import ArtifactQueryService
from relay_teams.agents.tasks.enums import TaskArtifactPhase
from relay_teams.agents.tasks.models import TaskArtifact, TaskArtifactSummary
from relay_teams.interfaces.server.deps import get_artifact_query_service
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(tags=["Artifacts"])


@router.get(
    "/runs/{run_id}/tasks/{task_id}/artifact",
    response_model=TaskArtifact,
)
async def get_task_artifact(
    _run_id: RequiredIdentifierStr = Path(alias="run_id"),
    task_id: RequiredIdentifierStr = Path(),
    service: ArtifactQueryService = Depends(get_artifact_query_service),
) -> TaskArtifact:
    artifact = service.get_artifact(task_id=task_id)
    if artifact is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Artifact not found")
    return artifact


@router.get(
    "/runs/{run_id}/tasks/{task_id}/artifact/entries",
)
async def get_task_artifact_entries(
    _run_id: RequiredIdentifierStr = Path(alias="run_id"),
    task_id: RequiredIdentifierStr = Path(),
    phase: TaskArtifactPhase | None = None,
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    service: ArtifactQueryService = Depends(get_artifact_query_service),
) -> dict[str, object]:
    entries, total = service.query_entries(
        task_id=task_id,
        phase=phase,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return {
        "task_id": task_id,
        "items": [entry.model_dump(mode="json") for entry in entries],
        "total": total,
        "next_offset": (offset + limit) if (offset + limit) < total else None,
    }


@router.get(
    "/runs/{run_id}/tasks/{task_id}/artifact/summary",
    response_model=TaskArtifactSummary,
)
async def get_task_artifact_summary(
    _run_id: RequiredIdentifierStr = Path(alias="run_id"),
    task_id: RequiredIdentifierStr = Path(),
    service: ArtifactQueryService = Depends(get_artifact_query_service),
) -> TaskArtifactSummary:
    summary = service.get_artifact_summary(task_id=task_id)
    if summary is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Artifact summary not found")
    return summary
