# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import JsonValue

from relay_teams.interfaces.server.deps import get_github_trigger_service
from relay_teams.triggers import GitHubTriggerService

router = APIRouter(prefix="/triggers", tags=["Triggers"])


@router.post("/github/deliveries")
async def handle_github_delivery(
    request: Request,
    service: Annotated[GitHubTriggerService, Depends(get_github_trigger_service)],
) -> dict[str, JsonValue]:
    body = await request.body()
    headers = {str(key): str(value) for key, value in request.headers.items()}
    return service.handle_inbound_github_delivery(headers=headers, body=body)
