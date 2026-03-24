# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from urllib.parse import unquote
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.interfaces.server.deps import get_workspace_service
from agent_teams.workspace import (
    WorkspaceDiffFile,
    WorkspaceDiffListing,
    WorkspaceRecord,
    WorkspaceService,
    WorkspaceSnapshot,
    WorkspaceTreeListing,
    pick_workspace_directory,
)

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str = Field(min_length=1)
    root_path: str = Field(min_length=1)


class PickWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceRecord | None = None


class PickWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_path: str | None = Field(default=None, min_length=1)


class ForkWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


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


@router.post("/pick", response_model=PickWorkspaceResponse)
def pick_workspace(
    req: PickWorkspaceRequest | None = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> PickWorkspaceResponse:
    if req is not None and req.root_path is not None:
        selected_root = Path(req.root_path)
    else:
        try:
            selected_root = pick_workspace_directory()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if selected_root is None:
        return PickWorkspaceResponse(workspace=None)

    try:
        workspace = service.create_workspace_for_root(root_path=selected_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PickWorkspaceResponse(workspace=workspace)


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


@router.get("/{workspace_id}/snapshot", response_model=WorkspaceSnapshot)
def get_workspace_snapshot(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSnapshot:
    try:
        return service.get_workspace_snapshot(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/tree", response_model=WorkspaceTreeListing)
def get_workspace_tree_listing(
    workspace_id: str,
    path: Annotated[str, Query()] = ".",
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceTreeListing:
    try:
        return service.get_workspace_tree_listing(
            workspace_id,
            directory_path=path,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/diffs", response_model=WorkspaceDiffListing)
def get_workspace_diffs(
    workspace_id: str,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDiffListing:
    try:
        return service.get_workspace_diffs(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/diff", response_model=WorkspaceDiffFile)
def get_workspace_diff_file(
    workspace_id: str,
    path: Annotated[str, Query(min_length=1)],
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDiffFile:
    try:
        return service.get_workspace_diff_file(
            workspace_id,
            path=unquote(path),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    remove_worktree: Annotated[bool, Query()] = False,
    service: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, str]:
    try:
        service.delete_workspace_with_options(
            workspace_id=workspace_id,
            remove_worktree=remove_worktree,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{workspace_id}:fork", response_model=WorkspaceRecord)
def fork_workspace(
    workspace_id: str,
    req: ForkWorkspaceRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.fork_workspace(
            source_workspace_id=workspace_id,
            name=req.name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
