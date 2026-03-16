# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agent_teams.interfaces.server.deps import get_mcp_service
from agent_teams.mcp.mcp_models import McpServerSummary, McpServerToolsSummary
from agent_teams.mcp.mcp_service import McpService

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/servers")
def list_mcp_servers(
    service: McpService = Depends(get_mcp_service),
) -> list[McpServerSummary]:
    return list(service.list_servers())


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
