# -*- coding: utf-8 -*-
from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.interfaces.server.deps import get_task_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.validation import RequiredIdentifierStr

from relay_teams.agents.tasks.models import (
    TaskHandoff,
    TaskLifecyclePolicy,
    TaskRecord,
    TaskSpec,
    VerificationPlan,
)

router = APIRouter(prefix="/tasks", tags=["Tasks"])


class CreateTasksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks: list[TaskDraft] = Field(min_length=1)


class UpdateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str | None = None
    title: str | None = None
    spec: TaskSpec | None = None
    verification: VerificationPlan | None = None
    lifecycle: TaskLifecyclePolicy | None = None
    handoff: TaskHandoff | None = None


@router.get("", response_model=list[TaskRecord])
async def list_tasks(
    service: TaskOrchestrationService = Depends(get_task_service),
) -> list[TaskRecord]:
    return list(await service.list_tasks_async())


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
async def list_tasks_for_run(
    run_id: RequiredIdentifierStr,
    include_root: bool = False,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return await service.list_delegated_tasks_async(
            run_id=run_id,
            include_root=include_root,
        )
    except KeyError as exc:
        raise http_exception_for(exc) from exc


@router.get("/{task_id}", response_model=TaskRecord)
async def get_task(
    task_id: RequiredIdentifierStr,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> TaskRecord:
    try:
        return await service.get_task_async(task_id=task_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Task not found") from exc


@router.patch("/{task_id}")
async def update_task_by_id(
    task_id: RequiredIdentifierStr,
    req: UpdateTaskRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        return await service.update_task_async(
            run_id=None,
            task_id=task_id,
            update=TaskUpdate(
                objective=req.objective,
                title=req.title,
                spec=req.spec,
                verification=req.verification,
                lifecycle=req.lifecycle,
                handoff=req.handoff,
            ),
        )
    except (KeyError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((ValueError, 400),),
        ) from exc
