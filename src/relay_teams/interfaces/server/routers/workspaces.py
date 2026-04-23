# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from urllib.parse import unquote
from pydantic import BaseModel, ConfigDict, Field, model_validator

from relay_teams.validation import require_force_delete
from relay_teams.interfaces.server.deps import get_workspace_service
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.validation import RequiredIdentifierStr
from relay_teams.workspace import (
    WorkspaceMountRecord,
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

    workspace_id: RequiredIdentifierStr
    root_path: str | None = Field(default=None, min_length=1)
    default_mount_name: RequiredIdentifierStr | None = None
    mounts: tuple[WorkspaceMountRecord, ...] | None = None

    @model_validator(mode="after")
    def _validate_mount_source(self) -> CreateWorkspaceRequest:
        has_root_path = self.root_path is not None
        has_mounts = self.mounts is not None
        if has_root_path == has_mounts:
            raise ValueError("Provide exactly one of root_path or mounts")
        return self


class UpdateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_mount_name: RequiredIdentifierStr
    mounts: tuple[WorkspaceMountRecord, ...]


class PickWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: WorkspaceRecord | None = None


class PickWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_path: str | None = Field(default=None, min_length=1)


class ForkWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    start_ref: str | None = Field(default=None, min_length=1)


@router.post("", response_model=WorkspaceRecord)
async def create_workspace(
    req: CreateWorkspaceRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.create_workspace(
            workspace_id=req.workspace_id,
            root_path=Path(req.root_path) if req.root_path is not None else None,
            mounts=req.mounts,
            default_mount_name=req.default_mount_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/pick", response_model=PickWorkspaceResponse)
async def pick_workspace(
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
async def list_workspaces(
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceRecord]:
    return list(service.list_workspaces())


@router.get("/{workspace_id}", response_model=WorkspaceRecord)
async def get_workspace(
    workspace_id: RequiredIdentifierStr,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.get_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc


@router.put("/{workspace_id}", response_model=WorkspaceRecord)
async def update_workspace(
    workspace_id: RequiredIdentifierStr,
    req: UpdateWorkspaceRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.update_workspace(
            workspace_id,
            mounts=req.mounts,
            default_mount_name=req.default_mount_name,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{workspace_id}:open-root")
async def open_workspace_root(
    workspace_id: RequiredIdentifierStr,
    mount: Annotated[str | None, Query()] = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, str]:
    try:
        if mount is None:
            _ = service.open_workspace_root(workspace_id)
        else:
            _ = service.open_workspace_root(workspace_id, mount_name=mount)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/snapshot", response_model=WorkspaceSnapshot)
async def get_workspace_snapshot(
    workspace_id: RequiredIdentifierStr,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceSnapshot:
    try:
        return service.get_workspace_snapshot(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/tree", response_model=WorkspaceTreeListing)
async def get_workspace_tree_listing(
    workspace_id: RequiredIdentifierStr,
    path: Annotated[str, Query()] = ".",
    mount: Annotated[str | None, Query()] = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceTreeListing:
    try:
        if mount is None:
            return service.get_workspace_tree_listing(
                workspace_id,
                directory_path=path,
            )
        return service.get_workspace_tree_listing(
            workspace_id,
            directory_path=path,
            mount_name=mount,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/diffs", response_model=WorkspaceDiffListing)
async def get_workspace_diffs(
    workspace_id: RequiredIdentifierStr,
    mount: Annotated[str | None, Query()] = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDiffListing:
    try:
        if mount is None:
            return service.get_workspace_diffs(workspace_id)
        return service.get_workspace_diffs(workspace_id, mount_name=mount)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/diff", response_model=WorkspaceDiffFile)
async def get_workspace_diff_file(
    workspace_id: RequiredIdentifierStr,
    path: Annotated[str, Query(min_length=1)],
    mount: Annotated[str | None, Query()] = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceDiffFile:
    try:
        if mount is None:
            return service.get_workspace_diff_file(
                workspace_id,
                path=unquote(path),
            )
        return service.get_workspace_diff_file(
            workspace_id,
            path=unquote(path),
            mount_name=mount,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{workspace_id}/preview-file")
async def get_workspace_preview_file(
    workspace_id: RequiredIdentifierStr,
    path: Annotated[str, Query(min_length=1)],
    mount: Annotated[str | None, Query()] = None,
    service: WorkspaceService = Depends(get_workspace_service),
) -> FileResponse:
    try:
        if mount is None:
            resolved_path, media_type = service.get_workspace_image_preview_file(
                workspace_id,
                path=unquote(path),
            )
        else:
            resolved_path, media_type = service.get_workspace_image_preview_file(
                workspace_id,
                path=unquote(path),
                mount_name=mount,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        path=resolved_path,
        filename=resolved_path.name,
        media_type=media_type,
    )


@router.delete("/{workspace_id}")
async def delete_workspace(
    workspace_id: RequiredIdentifierStr,
    remove_directory: Annotated[bool, Query()] = False,
    remove_worktree: Annotated[bool, Query()] = False,
    req: DeleteRequest | None = Body(default=None),
    service: WorkspaceService = Depends(get_workspace_service),
) -> dict[str, str]:
    try:
        should_remove_directory = remove_directory or remove_worktree
        if should_remove_directory:
            require_force_delete(
                req.force if req is not None else False,
                message="Cannot remove workspace directory without force",
            )
        service.delete_workspace_with_options(
            workspace_id=workspace_id,
            remove_directory=should_remove_directory,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{workspace_id}:fork", response_model=WorkspaceRecord)
async def fork_workspace(
    workspace_id: RequiredIdentifierStr,
    req: ForkWorkspaceRequest,
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceRecord:
    try:
        return service.fork_workspace(
            source_workspace_id=workspace_id,
            name=req.name,
            start_ref=req.start_ref,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
