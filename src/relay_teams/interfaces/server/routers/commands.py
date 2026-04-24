# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import JsonValue

from relay_teams.commands import (
    CommandCatalogResponse,
    CommandCreateRequest,
    CommandCreateResponse,
    CommandDetail,
    CommandManagementService,
    CommandModeNotAllowed,
    CommandRegistry,
    CommandResolveRequest,
    CommandResolveResponse,
    CommandSummary,
    CommandUpdateRequest,
    CommandUpdateResponse,
)
from relay_teams.commands.command_models import (
    command_detail_from_definition,
    command_summary_from_definition,
)
from relay_teams.interfaces.server.deps import (
    get_command_registry,
    get_workspace_service,
)
from relay_teams.logger import get_logger, log_event
from relay_teams.validation import RequiredIdentifierStr
from relay_teams.workspace import WorkspaceService

LOGGER = get_logger(__name__)
router = APIRouter(prefix="/system", tags=["Commands"])


@router.get("/commands", response_model=list[CommandSummary])
def list_commands(
    workspace_id: Annotated[RequiredIdentifierStr, Query()],
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> list[CommandSummary]:
    workspace_root = _resolve_workspace_root(
        workspace_id=workspace_id,
        workspace_service=workspace_service,
    )
    return [
        command_summary_from_definition(command)
        for command in registry.list_commands(workspace_root=workspace_root)
    ]


@router.get("/commands:catalog", response_model=CommandCatalogResponse)
def catalog_commands(
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> CommandCatalogResponse:
    return CommandManagementService(
        registry=registry,
        workspace_service=workspace_service,
    ).catalog()


@router.post("/commands", response_model=CommandCreateResponse)
def create_command(
    req: CommandCreateRequest,
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> CommandCreateResponse:
    service = CommandManagementService(
        registry=registry,
        workspace_service=workspace_service,
    )
    try:
        return service.create_command(req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/commands", response_model=CommandUpdateResponse)
def update_command(
    req: CommandUpdateRequest,
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> CommandUpdateResponse:
    service = CommandManagementService(
        registry=registry,
        workspace_service=workspace_service,
    )
    try:
        return service.update_command(req)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/commands:resolve", response_model=CommandResolveResponse)
def resolve_command(
    req: CommandResolveRequest,
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> CommandResolveResponse:
    workspace_root = _resolve_workspace_root(
        workspace_id=req.workspace_id,
        workspace_service=workspace_service,
    )
    cwd = Path(req.cwd).expanduser().resolve() if req.cwd is not None else None
    try:
        result = registry.resolve(
            raw_text=req.raw_text,
            mode=req.mode,
            workspace_root=workspace_root,
            cwd=cwd,
        )
    except CommandModeNotAllowed as exc:
        _log_resolve_result(
            workspace_id=req.workspace_id,
            result=CommandResolveResponse(
                matched=True,
                raw_text=req.raw_text,
                parsed_name=exc.command_name,
            ),
            rejected_mode=exc.mode,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _log_resolve_result(workspace_id=req.workspace_id, result=result)
    return result


@router.get("/commands/{name:path}", response_model=CommandDetail)
def get_command(
    name: str,
    workspace_id: Annotated[RequiredIdentifierStr, Query()],
    registry: CommandRegistry = Depends(get_command_registry),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> CommandDetail:
    workspace_root = _resolve_workspace_root(
        workspace_id=workspace_id,
        workspace_service=workspace_service,
    )
    command = registry.get_command(name, workspace_root=workspace_root)
    if command is None:
        raise HTTPException(status_code=404, detail="Command not found")
    return command_detail_from_definition(command)


def _resolve_workspace_root(
    *,
    workspace_id: str,
    workspace_service: WorkspaceService,
) -> Optional[Path]:
    try:
        workspace = workspace_service.get_workspace(workspace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Workspace not found") from exc
    return workspace.root_path


def _log_resolve_result(
    *,
    workspace_id: str,
    result: CommandResolveResponse,
    rejected_mode: Optional[str] = None,
) -> None:
    payload: dict[str, JsonValue] = {
        "workspace_id": workspace_id,
        "matched": result.matched,
        "raw_command_text": result.raw_text,
        "parsed_name": result.parsed_name,
        "resolved_name": result.resolved_name,
        "expanded_prompt_length": result.expanded_prompt_length,
    }
    if rejected_mode is not None:
        payload["rejected_mode"] = rejected_mode
    log_event(
        LOGGER,
        logging.INFO,
        event="commands.resolve",
        message="Slash command resolved",
        payload=payload,
    )
