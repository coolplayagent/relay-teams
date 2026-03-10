# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.interfaces.server.deps import get_workflow_service
from agent_teams.workflow.constants import CUSTOM_WORKFLOW_ID
from agent_teams.workflow.orchestration_service import WorkflowOrchestrationService
from agent_teams.workflow.spec import WorkflowTaskSpec

router = APIRouter(prefix="/workflows", tags=["Workflows"])

DispatchAction = str


class CreateWorkflowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    workflow_id: str = CUSTOM_WORKFLOW_ID
    tasks: list[WorkflowTaskSpec] | None = None


class DispatchTasksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: DispatchAction
    feedback: str = ""
    max_dispatch: int = 1


@router.post("/runs/{run_id}")
def create_workflow_for_run(
    run_id: str,
    req: CreateWorkflowRequest,
    service: WorkflowOrchestrationService = Depends(get_workflow_service),
) -> dict[str, object]:
    try:
        return service.create_workflow_graph(
            run_id=run_id,
            objective=req.objective,
            workflow_id=req.workflow_id,
            tasks=req.tasks,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/runs/{run_id}/{workflow_id}")
def get_workflow_status_for_run(
    run_id: str,
    workflow_id: str,
    service: WorkflowOrchestrationService = Depends(get_workflow_service),
) -> dict[str, object]:
    try:
        return service.get_workflow_status(run_id=run_id, workflow_id=workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/{workflow_id}/dispatch")
async def dispatch_tasks_for_run(
    run_id: str,
    workflow_id: str,
    req: DispatchTasksRequest,
    service: WorkflowOrchestrationService = Depends(get_workflow_service),
) -> dict[str, object]:
    try:
        return await service.dispatch_tasks(
            run_id=run_id,
            workflow_id=workflow_id,
            action=req.action,
            feedback=req.feedback,
            max_dispatch=req.max_dispatch,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
