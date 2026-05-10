# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from threading import Lock
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

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
_TOOLS_ROUTE_CACHE_SECONDS = 0.25
_TOOLS_ROUTE_LOCK = Lock()
_TOOLS_ROUTE_CACHE: dict[str, "_CachedToolsRouteResult"] = {}
_TOOLS_ROUTE_IN_FLIGHT: dict[str, asyncio.Task[McpServerToolsSummary]] = {}


class _CachedToolsRouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: McpServerToolsSummary
    expires_at: float


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
        return await _list_mcp_server_tools_with_route_guard(server_name, service)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/servers/{server_name}/tools:refresh")
async def refresh_mcp_server_tools(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerToolsSummary:
    try:
        return service.refresh_server_tools(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/servers/{server_name}/test")
async def test_mcp_server_connection(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerConnectionTestResult:
    try:
        return await service.test_server_connection(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _list_mcp_server_tools_with_route_guard(
    server_name: str,
    service: McpService,
) -> McpServerToolsSummary:
    normalized_name = server_name.strip()
    loop = asyncio.get_running_loop()
    task_to_await: asyncio.Task[McpServerToolsSummary] | None = None
    created_task: asyncio.Task[McpServerToolsSummary] | None = None
    with _TOOLS_ROUTE_LOCK:
        cached = _TOOLS_ROUTE_CACHE.get(normalized_name)
        if cached is not None:
            if cached.expires_at > time.monotonic():
                return cached.summary
            del _TOOLS_ROUTE_CACHE[normalized_name]
        existing_task = _TOOLS_ROUTE_IN_FLIGHT.get(normalized_name)
        if existing_task is not None:
            if existing_task.done():
                del _TOOLS_ROUTE_IN_FLIGHT[normalized_name]
            elif existing_task.get_loop() == loop:
                task_to_await = existing_task
        if task_to_await is None:
            created_task = loop.create_task(service.list_server_tools(server_name))
            _TOOLS_ROUTE_IN_FLIGHT[normalized_name] = created_task
            task_to_await = created_task

    try:
        summary = await asyncio.shield(task_to_await)
    finally:
        if created_task is not None:
            with _TOOLS_ROUTE_LOCK:
                if _TOOLS_ROUTE_IN_FLIGHT.get(normalized_name) is created_task:
                    del _TOOLS_ROUTE_IN_FLIGHT[normalized_name]
    if created_task is not None:
        with _TOOLS_ROUTE_LOCK:
            _TOOLS_ROUTE_CACHE[normalized_name] = _CachedToolsRouteResult(
                summary=summary,
                expires_at=time.monotonic() + _TOOLS_ROUTE_CACHE_SECONDS,
            )
    return summary
