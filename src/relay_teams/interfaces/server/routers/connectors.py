# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from relay_teams.binary_tools import (
    BinaryToolDownloadJob,
    BinaryToolListResponse,
    BinaryToolService,
    UnsupportedBinaryToolError,
)
from relay_teams.connector import (
    ConnectorListResponse,
    ConnectorService,
    ConnectorTestResult,
)
from relay_teams.interfaces.server.deps import (
    get_binary_tool_service,
    get_connector_service,
)
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get("", response_model=ConnectorListResponse)
async def list_connectors(
    service: Annotated[ConnectorService, Depends(get_connector_service)],
) -> ConnectorListResponse:
    return await service.list_connectors()


@router.post("/{connector_id}:test", response_model=ConnectorTestResult)
async def test_connector(
    connector_id: RequiredIdentifierStr,
    service: Annotated[ConnectorService, Depends(get_connector_service)],
) -> ConnectorTestResult:
    try:
        return await service.test_connector(connector_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/runtime-tools", response_model=BinaryToolListResponse)
async def list_runtime_tools(
    service: Annotated[BinaryToolService, Depends(get_binary_tool_service)],
) -> BinaryToolListResponse:
    return await service.list_tools()


@router.post(
    "/runtime-tools/{tool_id}:download",
    response_model=BinaryToolDownloadJob,
)
async def download_runtime_tool(
    tool_id: RequiredIdentifierStr,
    service: Annotated[BinaryToolService, Depends(get_binary_tool_service)],
) -> BinaryToolDownloadJob:
    try:
        return await service.start_download(tool_id)
    except UnsupportedBinaryToolError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/runtime-tools/downloads/{job_id}",
    response_model=BinaryToolDownloadJob,
)
async def get_runtime_tool_download(
    job_id: RequiredIdentifierStr,
    service: Annotated[BinaryToolService, Depends(get_binary_tool_service)],
) -> BinaryToolDownloadJob:
    try:
        return service.get_download_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
