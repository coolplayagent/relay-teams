# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskDraft,
    TaskOrchestrationService,
    TaskUpdate,
)
from relay_teams.interfaces.server.deps import get_task_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.validation import RequiredIdentifierStr

from relay_teams.agents.tasks.models import TaskRecord

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class CreateTasksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskDraft] = Field(min_length=1)


class UpdateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = None
    title: str | None = None


@router.get("", response_model=list[TaskRecord])
def list_tasks(
    service: TaskOrchestrationService = Depends(get_task_service),
) -> list[TaskRecord]:
    return list(service.list_tasks())


@router.post("/runs/{run_id}")
async def create_tasks_for_run(
    run_id: RequiredIdentifierStr,
    req: CreateTasksRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return await service.create_tasks(
            run_id=run_id,
            tasks=req.tasks,
        )
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc


@router.get("/runs/{run_id}")
def list_tasks_for_run(
    run_id: RequiredIdentifierStr,
    include_root: bool = False,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return service.list_delegated_tasks(run_id=run_id, include_root=include_root)
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.get("/{task_id}", response_model=TaskRecord)
def get_task(
    task_id: RequiredIdentifierStr,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> TaskRecord:
    try:
        return service.get_task(task_id=task_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Task not found") from exc


@router.patch("/{task_id}")
def update_task_by_id(
    task_id: RequiredIdentifierStr,
    req: UpdateTaskRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return service.update_task(
            run_id=None,
            task_id=task_id,
            update=TaskUpdate(
                objective=req.objective,
                title=req.title,
            ),
        )
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc
