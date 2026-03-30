from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import JsonValue

from agent_teams.automation import (
    AutomationFeishuBindingCandidate,
    AutomationProjectCreateInput,
    AutomationProjectNameConflictError,
    AutomationProjectRecord,
    AutomationProjectStatus,
    AutomationProjectUpdateInput,
    AutomationService,
)
from agent_teams.interfaces.server.deps import get_automation_service

router = APIRouter(prefix="/automation", tags=["Automation"])


@router.get("/feishu-bindings", response_model=list[AutomationFeishuBindingCandidate])
def list_feishu_bindings(
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationFeishuBindingCandidate]:
    return list(service.list_feishu_bindings())


@router.post("/projects", response_model=AutomationProjectRecord)
def create_project(
    req: AutomationProjectCreateInput,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return service.create_project(req)
    except AutomationProjectNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/projects", response_model=list[AutomationProjectRecord])
def list_projects(
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationProjectRecord]:
    return list(service.list_projects())


@router.get("/projects/{automation_project_id}", response_model=AutomationProjectRecord)
def get_project(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return service.get_project(automation_project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch(
    "/projects/{automation_project_id}", response_model=AutomationProjectRecord
)
def update_project(
    automation_project_id: str,
    req: AutomationProjectUpdateInput,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return service.update_project(automation_project_id, req)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AutomationProjectNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/projects/{automation_project_id}")
def delete_project(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> dict[str, JsonValue]:
    try:
        service.delete_project(automation_project_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/projects/{automation_project_id}:run")
async def run_project(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> dict[str, JsonValue]:
    try:
        return service.run_now(automation_project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/projects/{automation_project_id}:enable", response_model=AutomationProjectRecord
)
def enable_project(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return service.set_project_status(
            automation_project_id,
            AutomationProjectStatus.ENABLED,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/projects/{automation_project_id}:disable",
    response_model=AutomationProjectRecord,
)
def disable_project(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationProjectRecord:
    try:
        return service.set_project_status(
            automation_project_id,
            AutomationProjectStatus.DISABLED,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{automation_project_id}/sessions")
def list_project_sessions(
    automation_project_id: str,
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[dict[str, object]]:
    try:
        return list(service.list_project_sessions(automation_project_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
