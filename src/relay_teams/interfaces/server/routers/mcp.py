# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from relay_teams.interfaces.server.deps import get_mcp_service
from relay_teams.mcp.mcp_models import (
    McpServerAddRequest,
    McpServerAddResult,
    McpServerConfigResult,
    McpServerConnectionTestResult,
    McpServerEnabledUpdateRequest,
    McpServerSummary,
    McpServerToolsSummary,
    McpServerUpdateRequest,
)
from relay_teams.mcp.mcp_service import McpService

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/servers")
async def list_mcp_servers(
    service: McpService = Depends(get_mcp_service),
) -> list[McpServerSummary]:
    return list(service.list_servers())


@router.post("/servers")
async def add_mcp_server(
    request: McpServerAddRequest,
    service: McpService = Depends(get_mcp_service),
) -> McpServerAddResult:
    try:
        return service.add_server(
            name=request.name,
            server_config=request.config,
            overwrite=request.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/servers/{server_name}")
async def get_mcp_server_config(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerConfigResult:
    try:
        return service.get_server_config(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/servers/{server_name}")
async def update_mcp_server(
    server_name: str,
    request: McpServerUpdateRequest,
    service: McpService = Depends(get_mcp_service),
) -> McpServerConfigResult:
    try:
        return service.update_server(server_name, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/servers/{server_name}/enabled")
async def set_mcp_server_enabled(
    server_name: str,
    request: McpServerEnabledUpdateRequest,
    service: McpService = Depends(get_mcp_service),
) -> McpServerSummary:
    try:
        return service.set_server_enabled(server_name, request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/servers/{server_name}/tools")
async def list_mcp_server_tools(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerToolsSummary:
    try:
        return await service.list_server_tools(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load MCP tools for '{server_name}': {exc}",
        ) from exc


@router.post("/servers/{server_name}/test")
async def test_mcp_server_connection(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerConnectionTestResult:
    try:
        return await service.test_server_connection(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
