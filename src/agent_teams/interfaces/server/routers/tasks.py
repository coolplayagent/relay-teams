# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from agent_teams.agents.orchestration.task_orchestration_service import (
    TaskDraft,
    TaskOrchestrationService,
    TaskUpdate,
)
from agent_teams.interfaces.server.deps import get_task_repo, get_task_service

from agent_teams.agents.tasks.task_repository import TaskRepository
from agent_teams.agents.tasks.models import TaskRecord

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class CreateTasksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskDraft] = Field(min_length=1)


class UpdateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = None
    title: str | None = None


class DispatchTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1)
    prompt: str = ""


@router.get("", response_model=list[TaskRecord])
def list_tasks(task_repo: TaskRepository = Depends(get_task_repo)) -> list[TaskRecord]:
    return list(task_repo.list_all())


@router.post("/runs/{run_id}")
async def create_tasks_for_run(
    run_id: str,
    req: CreateTasksRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return await service.create_tasks(
            run_id=run_id,
            tasks=req.tasks,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}")
def list_tasks_for_run(
    run_id: str,
    include_root: bool = False,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return service.list_delegated_tasks(run_id=run_id, include_root=include_root)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}", response_model=TaskRecord)
def get_task(
    task_id: str,
    task_repo: TaskRepository = Depends(get_task_repo),
) -> TaskRecord:
    try:
        return task_repo.get(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@router.patch("/{task_id}")
def update_task_by_id(
    task_id: str,
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
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{task_id}/dispatch")
async def dispatch_task_by_id(
    task_id: str,
    req: DispatchTaskRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return await service.dispatch_task(
            run_id=None,
            task_id=task_id,
            role_id=req.role_id,
            prompt=req.prompt,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
