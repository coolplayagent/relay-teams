from __future__ import annotations

import json
import logging
import time
from typing import Annotated, ClassVar, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_teams.interfaces.server.deps import get_run_service
from agent_teams.logger import get_logger, log_event
from agent_teams.sessions.runs.manager import RunManager
from agent_teams.sessions.runs.enums import ApprovalMode, ExecutionMode, InjectionSource
from agent_teams.sessions.runs.models import IntentInput
from agent_teams.trace import bind_trace_context

logger = get_logger(__name__)
router = APIRouter(prefix="/runs", tags=["Runs"])


class CreateRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    execution_mode: ExecutionMode = ExecutionMode.AI
    approval_mode: ApprovalMode = ApprovalMode.STANDARD


class CreateRunResponse(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    run_id: str
    session_id: str


class InjectMessageRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    source: InjectionSource = InjectionSource.USER
    content: str = Field(min_length=1)


class ResolveToolApprovalRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    action: Literal["approve", "deny"]
    feedback: str = ""


class StopRunRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    scope: Literal["main", "subagent"] = "main"
    instance_id: str | None = None


class InjectSubagentRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)


@router.post("", response_model=CreateRunResponse)
def create_run(
    req: CreateRunRequest,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> CreateRunResponse:
    started = time.perf_counter()
    try:
        run_id, session_id = service.create_run(
            IntentInput(
                session_id=req.session_id,
                intent=req.intent,
                execution_mode=req.execution_mode,
                approval_mode=req.approval_mode,
            )
        )
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
                    "approval_mode": req.approval_mode.value,
                },
            )
        return CreateRunResponse(run_id=run_id, session_id=session_id)
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
    run_id: str,
    service: Annotated[RunManager, Depends(get_run_service)],
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
            )
            try:
                async for event in service.stream_run_events(run_id):
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
    run_id: str,
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
    run_id: str,
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


@router.post("/{run_id}/tool-approvals/{tool_call_id}/resolve")
def resolve_tool_approval(
    run_id: str,
    tool_call_id: str,
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
    run_id: str,
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
    run_id: str,
    service: Annotated[RunManager, Depends(get_run_service)],
) -> dict[str, str]:
    try:
        session_id = service.resume_run(run_id)
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
    run_id: str,
    instance_id: str,
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
