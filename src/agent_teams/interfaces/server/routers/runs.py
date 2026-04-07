from __future__ import annotations

import json
import logging
import time
from typing import Annotated, ClassVar, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.interfaces.server.deps import get_run_service
from agent_teams.logger import get_logger, log_event
from agent_teams.media import (
    ContentPart,
    InlineMediaContentPart,
)
from agent_teams.sessions.runs.run_manager import RunManager
from agent_teams.sessions.runs.enums import ExecutionMode, InjectionSource
from agent_teams.sessions.runs.run_models import (
    IntentInput,
    MediaGenerationConfig,
    RunKind,
    RunThinkingConfig,
)
from agent_teams.trace import bind_trace_context
from agent_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

logger = get_logger(__name__)
router = APIRouter(prefix="/runs", tags=["Runs"])


class CreateRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    session_id: RequiredIdentifierStr
    input: tuple[ContentPart, ...] = Field(default_factory=tuple)
    run_kind: RunKind = RunKind.CONVERSATION
    generation_config: MediaGenerationConfig | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    yolo: bool = False
    thinking: RunThinkingConfig = Field(default_factory=RunThinkingConfig)
    target_role_id: OptionalIdentifierStr = None


class CreateRunResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    run_id: RequiredIdentifierStr
    session_id: RequiredIdentifierStr
    target_role_id: OptionalIdentifierStr = None


class InjectMessageRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    source: InjectionSource = InjectionSource.USER
    content: str = Field(min_length=1)


class ResolveToolApprovalRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    action: Literal[
        "approve",
        "approve_once",
        "approve_exact",
        "approve_prefix",
        "deny",
    ]
    feedback: str = ""


class StopRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    scope: Literal["main", "subagent"] = "main"
    instance_id: OptionalIdentifierStr = None


class InjectSubagentRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


class StopBackgroundTaskResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    background_task: dict[str, object]


@router.post(
    "",
    response_model=CreateRunResponse,
    response_model_exclude_none=True,
)
async def create_run(
    request: Request,
    req: CreateRunRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> CreateRunResponse:
    started = time.perf_counter()
    try:
        normalized_input = req.input
        if any(isinstance(part, InlineMediaContentPart) for part in req.input):
            container = getattr(request.app.state, "container", None)
            if container is None:
                raise HTTPException(
                    status_code=503,
                    detail="Media uploads require the server container to be initialized",
                )
            session = container.session_service.get_session(req.session_id)
            normalized_input = container.media_asset_service.normalize_content_parts(
                session_id=req.session_id,
                workspace_id=session.workspace_id,
                parts=req.input,
            )
        if not normalized_input:
            raise HTTPException(status_code=400, detail="Run input cannot be empty")
        run_id, session_id = service.create_run(
            IntentInput(
                session_id=req.session_id,
                input=normalized_input,
                run_kind=req.run_kind,
                generation_config=req.generation_config,
                execution_mode=req.execution_mode,
                yolo=req.yolo,
                thinking=req.thinking,
                target_role_id=req.target_role_id,
            )
        )
        service.ensure_run_started(run_id)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.created",
                message="Run created",
                duration_ms=elapsed_ms,
                payload={
                    "execution_mode": req.execution_mode.value,
                    "yolo": req.yolo,
                },
            )
        return CreateRunResponse(
            run_id=run_id,
            session_id=session_id,
            target_role_id=req.target_role_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        log_event(
            logger,
            logging.WARNING,
            event="run.create.conflict",
            message="Failed to create run due to runtime conflict",
            payload={"session_id": req.session_id},
            exc_info=exc,
        )
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{run_id}/events")
async def stream_run_events(
    run_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
    after_event_id: int = 0,
) -> StreamingResponse:
    async def event_generator():
        event_count = 0
        started = time.perf_counter()
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.INFO,
                event="stream.opened",
                message="Run event stream opened",
                payload={"after_event_id": after_event_id},
            )
            try:
                async for event in service.stream_run_events(
                    run_id, after_event_id=after_event_id
                ):
                    event_count += 1
                    yield f"data: {event.model_dump_json()}\n\n"
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                log_event(
                    logger,
                    logging.INFO,
                    event="stream.closed",
                    message="Run event stream closed",
                    duration_ms=elapsed_ms,
                    payload={"event_count": event_count},
                )
            except KeyError as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    event="stream.not_found",
                    message="Run not found during stream start",
                    exc_info=exc,
                )
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            except Exception as exc:  # pragma: no cover - defensive path
                log_event(
                    logger,
                    logging.ERROR,
                    event="stream.failed",
                    message="Unexpected stream failure",
                    payload={"event_count": event_count},
                    exc_info=exc,
                )
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.post("/{run_id}/inject")
def inject_message(
    run_id: RequiredIdentifierStr,
    req: InjectMessageRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        result = service.inject_message(
            run_id=run_id, source=req.source, content=req.content
        )
        with bind_trace_context(trace_id=run_id, run_id=run_id):
            log_event(
                logger,
                logging.INFO,
                event="run.message.injected",
                message="Message injected to running agents",
                payload={"source": req.source.value, "length": len(req.content)},
            )
        return result.model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/tool-approvals")
def list_tool_approvals(
    run_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> list[dict[str, str]]:
    with bind_trace_context(trace_id=run_id, run_id=run_id):
        result = service.list_open_tool_approvals(run_id)
        log_event(
            logger,
            logging.INFO,
            event="tool.approval.listed",
            message="Listed open tool approvals",
            payload={"count": len(result)},
        )
        return result


@router.get("/{run_id}/background-tasks")
def list_background_tasks(
    run_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        return {"items": list(service.list_background_tasks(run_id))}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{run_id}/background-tasks/{background_task_id}")
def get_background_task(
    run_id: RequiredIdentifierStr,
    background_task_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, object]:
    try:
        return {
            "background_task": service.get_background_task(
                run_id=run_id,
                background_task_id=background_task_id,
            )
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{run_id}/background-tasks/{background_task_id}:stop",
    response_model=StopBackgroundTaskResponse,
)
async def stop_background_task(
    run_id: RequiredIdentifierStr,
    background_task_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> StopBackgroundTaskResponse:
    try:
        result = await service.stop_background_task(
            run_id=run_id,
            background_task_id=background_task_id,
        )
        return StopBackgroundTaskResponse(background_task=result)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{run_id}/tool-approvals/{tool_call_id}/resolve")
def resolve_tool_approval(
    run_id: RequiredIdentifierStr,
    tool_call_id: RequiredIdentifierStr,
    req: ResolveToolApprovalRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        service.resolve_tool_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            action=req.action,
            feedback=req.feedback,
        )
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, tool_call_id=tool_call_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="tool.approval.resolved",
                message="Tool approval resolved",
                payload={"action": req.action, "feedback_length": len(req.feedback)},
            )
        return {"status": "ok", "action": req.action}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/stop")
def stop_run(
    run_id: RequiredIdentifierStr,
    req: StopRunRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        if req.scope == "main":
            service.stop_run(run_id)
            with bind_trace_context(trace_id=run_id, run_id=run_id):
                log_event(
                    logger,
                    logging.WARNING,
                    event="run.stopped",
                    message="Run stop requested",
                )
            return {"status": "ok", "scope": "main"}
        if not req.instance_id:
            raise HTTPException(
                status_code=422,
                detail="instance_id is required when scope is subagent",
            )
        payload = service.stop_subagent(run_id, req.instance_id)
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, instance_id=req.instance_id
        ):
            log_event(
                logger,
                logging.WARNING,
                event="subagent.stopped",
                message="Subagent stop requested",
            )
        return {
            "status": "ok",
            "scope": "subagent",
            "instance_id": payload["instance_id"],
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{run_id}:resume")
async def resume_run(
    run_id: RequiredIdentifierStr,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        session_id = service.resume_run(run_id)
        service.ensure_run_started(run_id)
        with bind_trace_context(trace_id=run_id, run_id=run_id, session_id=session_id):
            log_event(
                logger,
                logging.INFO,
                event="run.resume.requested",
                message="Run resume requested",
            )
        return {"status": "ok", "run_id": run_id, "session_id": session_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/subagents/{instance_id}/inject")
def inject_subagent(
    run_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    req: InjectSubagentRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        service.inject_subagent_message(
            run_id=run_id,
            instance_id=instance_id,
            content=req.content,
        )
        with bind_trace_context(
            trace_id=run_id, run_id=run_id, instance_id=instance_id
        ):
            log_event(
                logger,
                logging.INFO,
                event="subagent.message.injected",
                message="Subagent follow-up message injected",
                payload={"length": len(req.content)},
            )
        return {"status": "ok", "instance_id": instance_id}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
