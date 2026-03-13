# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from agent_teams.interfaces.server.deps import get_workspace_service
from agent_teams.workspace import WorkspaceRecord, WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    root_path: str


@router.post("", response_model=WorkspaceRecord)
def create_workspace(
    req: CreateWorkspaceRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.create_workspace(
            workspace_id=req.workspace_id,
            root_path=Path(req.root_path),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=list[WorkspaceRecord])
def list_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceRecord]:
    return list(service.list_workspaces())


@router.get("/{workspace_id}", response_model=WorkspaceRecord)
def get_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.get_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
