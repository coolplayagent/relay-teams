# -*- coding: utf-8 -*-
from __future__ import annotations


from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from relay_teams.agents.orchestration.llm_evaluator import LLMEvaluator
from relay_teams.agents.orchestration.llm_evaluator_models import (
    LLMEvaluationRequest,
    LLMEvaluationResult,
)
from relay_teams.agents.orchestration.task_orchestration_service import (
    TaskOrchestrationService,
)
from relay_teams.agents.orchestration.task_contracts import TaskDraft, TaskUpdate
from relay_teams.interfaces.server.deps import get_llm_evaluator, get_task_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

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
    spec_artifact_id: OptionalIdentifierStr = None
    spec_source_task_id: OptionalIdentifierStr = None
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
                spec_artifact_id=req.spec_artifact_id,
                spec_source_task_id=req.spec_source_task_id,
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


@router.get("/{task_id}/spec-artifact")
async def get_task_spec_artifact(
    task_id: RequiredIdentifierStr,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        artifact = await service.get_task_spec_artifact_async(task_id=task_id)
    except KeyError as exc:
        raise http_exception_for(
            exc, key_error_detail="Spec artifact not found"
        ) from exc
    return artifact.model_dump(mode="json")


@router.get("/{task_id}/evidence-bundle")
async def get_task_evidence_bundle(
    task_id: RequiredIdentifierStr,
    service: TaskOrchestrationService = Depends(get_task_service),
) -> dict[str, JsonValue]:
    try:
        bundle = await service.get_task_evidence_bundle_async(task_id=task_id)
    except KeyError as exc:
        raise http_exception_for(
            exc, key_error_detail="Evidence bundle not found"
        ) from exc
    return bundle.model_dump(mode="json")


class EvaluateSpecRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_result: str | None = None


@router.post(
    "/{task_id}/evaluate-spec",
    response_model=LLMEvaluationResult,
)
async def evaluate_task_spec(
    task_id: RequiredIdentifierStr,
    req: EvaluateSpecRequest,
    service: TaskOrchestrationService = Depends(get_task_service),
    evaluator: LLMEvaluator = Depends(get_llm_evaluator),
) -> LLMEvaluationResult:
    try:
        record = await service.get_task_async(task_id=task_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Task not found") from exc

    spec = (
        record.envelope.spec
        if isinstance(record.envelope.spec, TaskSpec)
        else TaskSpec()
    )
    eval_request = LLMEvaluationRequest(
        task_id=task_id,
        spec_summary=spec.summary,
        requirements=spec.requirements,
        constraints=spec.constraints,
        acceptance_criteria=spec.acceptance_criteria,
        evidence_expectations=spec.evidence_expectations,
        task_result=req.task_result,
    )
    return await evaluator.evaluate_spec_quality(eval_request)
