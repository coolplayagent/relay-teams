# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from threading import Lock
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

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
from relay_teams.mcp.mcp_service import (
    McpService,
    McpToolLoadBusyError,
    McpToolLoadUnavailableError,
)

router = APIRouter(prefix="/mcp", tags=["MCP"])
MCP_TOOLS_ROUTE_CONCURRENCY_ENV = "RELAY_TEAMS_MCP_TOOLS_ROUTE_CONCURRENCY"
MCP_TOOLS_ROUTE_MIN_INTERVAL_MS_ENV = "RELAY_TEAMS_MCP_TOOLS_ROUTE_MIN_INTERVAL_MS"
DEFAULT_MCP_TOOLS_ROUTE_CONCURRENCY = 2
DEFAULT_MCP_TOOLS_ROUTE_MIN_INTERVAL_MS = 0
_mcp_tools_route_lock = Lock()


class _McpToolsRouteState:
    def __init__(self) -> None:
        self.active_count = 0
        self.last_started_monotonic = 0.0


_mcp_tools_route_state = _McpToolsRouteState()


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
) -> Response:
    if not _try_enter_mcp_tools_route():
        raise HTTPException(
            status_code=429,
            detail="MCP tool listing is busy",
            headers={"Retry-After": "1"},
        )
    try:
        summary = await service.list_server_tools(server_name)
        return Response(
            content=summary.model_dump_json(),
            media_type="application/json",
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except McpToolLoadBusyError as exc:
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": "1"},
        ) from exc
    except McpToolLoadUnavailableError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load MCP tools for '{server_name}': {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load MCP tools for '{server_name}': {exc}",
        ) from exc
    finally:
        _release_mcp_tools_route()


@router.post("/servers/{server_name}/tools:refresh")
async def refresh_mcp_server_tools(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerToolsSummary:
    try:
        return service.refresh_server_tools(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/servers/{server_name}/test")
async def test_mcp_server_connection(
    server_name: str,
    service: McpService = Depends(get_mcp_service),
) -> McpServerConnectionTestResult:
    try:
        return await service.test_server_connection(server_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _try_enter_mcp_tools_route() -> bool:
    limit = _positive_int_env(
        MCP_TOOLS_ROUTE_CONCURRENCY_ENV,
        DEFAULT_MCP_TOOLS_ROUTE_CONCURRENCY,
    )
    min_interval_seconds = (
        _non_negative_int_env(
            MCP_TOOLS_ROUTE_MIN_INTERVAL_MS_ENV,
            DEFAULT_MCP_TOOLS_ROUTE_MIN_INTERVAL_MS,
        )
        / 1000.0
    )
    with _mcp_tools_route_lock:
        now = time.monotonic()
        if (
            min_interval_seconds > 0
            and now - _mcp_tools_route_state.last_started_monotonic
            < min_interval_seconds
        ):
            return False
        if _mcp_tools_route_state.active_count >= limit:
            return False
        _mcp_tools_route_state.active_count += 1
        _mcp_tools_route_state.last_started_monotonic = now
        return True


def _release_mcp_tools_route() -> None:
    with _mcp_tools_route_lock:
        _mcp_tools_route_state.active_count = max(
            0,
            _mcp_tools_route_state.active_count - 1,
        )


def _positive_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _non_negative_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value.strip())
    except ValueError:
        return default
    return parsed if parsed >= 0 else default
