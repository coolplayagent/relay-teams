from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from relay_teams.automation.automation_models import (
    AutomationFeishuBindingCandidate,
    AutomationProjectCreateInput,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
)
from relay_teams.automation.automation_service import AutomationService
from relay_teams.automation.errors import AutomationProjectNameConflictError
from relay_teams.interfaces.server.deps import get_automation_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.validation import RequiredIdentifierStr

router = APIRouter(prefix="/automation", tags=["Automation"])


@router.get("/feishu-bindings", response_model=list[AutomationFeishuBindingCandidate])
async def list_feishu_bindings(
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationFeishuBindingCandidate]:
    bindings = await run_in_threadpool(service.list_feishu_bindings)
    return list(bindings)


@router.post("/projects", response_model=AutomationProjectRecord)
async def create_project(
    req: AutomationProjectCreateInput,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return await run_in_threadpool(service.create_project, req)
    except (AutomationProjectNameConflictError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((AutomationProjectNameConflictError, 409), (ValueError, 422)),
        ) from exc


@router.get("/projects", response_model=list[AutomationProjectRecord])
async def list_projects(
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationProjectRecord]:
    projects = await run_in_threadpool(service.list_projects)
    return list(projects)


@router.get("/projects/{automation_project_id}", response_model=AutomationProjectRecord)
async def get_project(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return await run_in_threadpool(service.get_project, automation_project_id)
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.patch(
    "/projects/{automation_project_id}", response_model=AutomationProjectRecord
)
async def update_project(
    automation_project_id: RequiredIdentifierStr,
    req: AutomationProjectUpdateInput,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return await run_in_threadpool(
            service.update_project,
            automation_project_id,
            req,
        )
    except (KeyError, AutomationProjectNameConflictError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((AutomationProjectNameConflictError, 409), (ValueError, 422)),
        ) from exc


@router.delete("/projects/{automation_project_id}")
async def delete_project(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
    req: DeleteRequest | None = Body(default=None),
) -> dict[str, JsonValue]:
    try:
        await run_in_threadpool(
            service.delete_project,
            automation_project_id,
            force=req.force if req is not None else False,
            cascade=req.cascade if req is not None else False,
        )
        return {"status": "ok"}
    except (KeyError, RuntimeError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 409),),
        ) from exc


@router.post("/projects/{automation_project_id}:run")
async def run_project(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> dict[str, JsonValue]:
    try:
        return await run_in_threadpool(service.run_now, automation_project_id)
    except (KeyError, RuntimeError) as exc:
        raise http_exception_for(
            exc,
            mappings=((RuntimeError, 409),),
        ) from exc


@router.post(
    "/projects/{automation_project_id}:enable", response_model=AutomationProjectRecord
)
async def enable_project(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return await run_in_threadpool(
            service.set_project_status,
            automation_project_id,
            AutomationProjectStatus.ENABLED,
        )
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 422),),
        ) from exc


@router.post(
    "/projects/{automation_project_id}:disable",
    response_model=AutomationProjectRecord,
)
async def disable_project(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return await run_in_threadpool(
            service.set_project_status,
            automation_project_id,
            AutomationProjectStatus.DISABLED,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{automation_project_id}/sessions")
async def list_project_sessions(
    automation_project_id: RequiredIdentifierStr,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[dict[str, object]]:
    try:
        sessions = await run_in_threadpool(
            service.list_project_sessions,
            automation_project_id,
        )
        return list(sessions)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
