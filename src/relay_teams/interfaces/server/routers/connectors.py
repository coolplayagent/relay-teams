# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from relay_teams.connector import (
    ConnectorListResponse,
    ConnectorService,
    ConnectorTestResult,
)
from relay_teams.interfaces.server.deps import get_connector_service
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
