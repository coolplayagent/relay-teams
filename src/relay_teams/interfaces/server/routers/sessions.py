from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

from relay_teams.interfaces.server.async_call import (
    call_maybe_async,
    call_maybe_async_in_session_read_thread,
)
from relay_teams.interfaces.server.deps import get_session_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
from relay_teams.logger import get_logger, log_event
from relay_teams.interfaces.server.write_models import DeleteRequest
from relay_teams.roles import SystemRolesUnavailableError
from relay_teams.sessions.session_service import SessionService
from relay_teams.sessions.session_models import (
    SessionCreateMetadata,
    SessionMetadataPatch,
    SessionMode,
    SessionRecord,
    normalize_session_create_metadata_input,
)
from relay_teams.validation import OptionalIdentifierStr, RequiredIdentifierStr

router = APIRouter(prefix="/sessions", tags=["Sessions"])
logger = get_logger(__name__)

SESSION_RECOVERY_TIMEOUT_SECONDS = 8.0
SESSION_TERMINAL_VIEW_TIMEOUT_SECONDS = 2.0
ParamT = ParamSpec("ParamT")
ResultT = TypeVar("ResultT")


async def _call_session_read(
    operation: str,
    function: Callable[ParamT, ResultT | Awaitable[ResultT]],
    /,
    *args: ParamT.args,
    **kwargs: ParamT.kwargs,
) -> ResultT:
    return await call_maybe_async_in_session_read_thread(
        operation,
        function,
        *args,
        **kwargs,
    )


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: OptionalIdentifierStr = None
    workspace_id: RequiredIdentifierStr
    metadata: SessionCreateMetadata | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: object) -> object:
        return normalize_session_create_metadata_input(value)


class UpdateSessionTopologyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_mode: SessionMode
    normal_root_role_id: OptionalIdentifierStr = None
    orchestration_preset_id: OptionalIdentifierStr = None


class UpdateAgentReflectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str


@router.post("", response_model=SessionRecord)
async def create_session(
    req: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:

        def _create_session() -> SessionRecord:
            return service.create_session(
                session_id=req.session_id,
                workspace_id=req.workspace_id,
                metadata=(
                    None if req.metadata is None else req.metadata.to_metadata_dict()
                ),
            )

        return await call_maybe_async(_create_session)
    except (SystemRolesUnavailableError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503), (ValueError, 422)),
        ) from exc


@router.get("", response_model=list[SessionRecord])
async def list_sessions(
    service: SessionService = Depends(get_session_service),
) -> list[SessionRecord]:
    def _list_sessions() -> tuple[SessionRecord, ...]:
        return service.list_sessions()

    records = await _call_session_read("sessions.list", _list_sessions)
    return list(records)


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return await _call_session_read("sessions.get", service.get_session, session_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc


@router.patch("/{session_id}")
async def update_session(
    session_id: RequiredIdentifierStr,
    req: SessionMetadataPatch,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        await call_maybe_async(service.update_session, session_id, req)
        return {"status": "ok"}
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 422),)) from exc


@router.post("/{session_id}/terminal-view")
async def mark_session_terminal_viewed(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        await asyncio.wait_for(
            _call_session_read(
                "sessions.terminal_view",
                service.mark_latest_terminal_run_viewed,
                session_id,
            ),
            timeout=SESSION_TERMINAL_VIEW_TIMEOUT_SECONDS,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except TimeoutError:
        log_event(
            logger,
            logging.WARNING,
            event="session.terminal_view.mark_timeout",
            message="Session terminal view marker timed out",
            payload={
                "session_id": session_id,
                "timeout_seconds": SESSION_TERMINAL_VIEW_TIMEOUT_SECONDS,
            },
        )
        return {"status": "deferred"}


@router.patch("/{session_id}/topology", response_model=SessionRecord)
async def update_session_topology(
    session_id: RequiredIdentifierStr,
    req: UpdateSessionTopologyRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:

        def _update_session_topology() -> SessionRecord:
            return service.update_session_topology(
                session_id,
                session_mode=req.session_mode,
                normal_root_role_id=req.normal_root_role_id,
                orchestration_preset_id=req.orchestration_preset_id,
            )

        return await call_maybe_async(_update_session_topology)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except SystemRolesUnavailableError as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503),),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{session_id}")
async def delete_session(
    session_id: RequiredIdentifierStr,
    req: DeleteRequest | None = Body(default=None),
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:

        def _delete_session() -> None:
            service.delete_session(
                session_id,
                force=req.force if req is not None else False,
                cascade=req.cascade if req is not None else False,
            )

        await call_maybe_async(_delete_session)
        return {"status": "ok"}
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/rounds")
async def get_session_rounds(
    session_id: RequiredIdentifierStr,
    limit: int = 8,
    cursor_run_id: OptionalIdentifierStr = None,
    timeline: bool = False,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    def _get_session_rounds() -> dict[str, object]:
        return service.get_session_rounds(
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
        )

    return await _call_session_read("sessions.rounds", _get_session_rounds)


@router.get("/{session_id}/recovery")
async def get_session_recovery(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await asyncio.wait_for(
            _call_session_read(
                "sessions.recovery",
                service.get_recovery_snapshot,
                session_id,
            ),
            timeout=SESSION_RECOVERY_TIMEOUT_SECONDS,
        )
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except TimeoutError as exc:
        log_event(
            logger,
            logging.WARNING,
            event="session.recovery.snapshot_timeout",
            message="Session recovery snapshot timed out",
            payload={
                "session_id": session_id,
                "timeout_seconds": SESSION_RECOVERY_TIMEOUT_SECONDS,
            },
        )
        raise HTTPException(
            status_code=503,
            detail="Session recovery snapshot timed out",
        ) from exc


@router.get("/{session_id}/rounds/{run_id}")
async def get_round(
    session_id: RequiredIdentifierStr,
    run_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await _call_session_read(
            "sessions.round",
            service.get_round,
            session_id,
            run_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/agents")
async def list_session_agents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        agents = await _call_session_read(
            "sessions.agents",
            service.list_agents_in_session,
            session_id,
        )
        return list(agents)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/subagents")
async def list_session_subagents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        subagents = await _call_session_read(
            "sessions.subagents",
            service.list_normal_mode_subagents,
            session_id,
        )
        return list(subagents)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/subagents/events")
async def stream_session_subagent_events(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
    after_event_id: int = 0,
) -> StreamingResponse:
    async def event_generator():
        event_count = 0
        started = time.perf_counter()
        log_event(
            logger,
            logging.INFO,
            event="session.subagent_stream.opened",
            message="Session subagent event stream opened",
            payload={"session_id": session_id, "after_event_id": after_event_id},
        )
        try:
            async for event in service.stream_normal_mode_subagent_events(
                session_id,
                after_event_id=after_event_id,
            ):
                event_count += 1
                yield f"data: {event.model_dump_json()}\n\n"
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                logger,
                logging.INFO,
                event="session.subagent_stream.closed",
                message="Session subagent event stream closed",
                duration_ms=elapsed_ms,
                payload={"session_id": session_id, "event_count": event_count},
            )
        except KeyError as exc:
            log_event(
                logger,
                logging.WARNING,
                event="session.subagent_stream.not_found",
                message="Session not found during subagent stream start",
                payload={"session_id": session_id},
                exc_info=exc,
            )
            yield f"data: {json.dumps({'error': 'Session not found'})}\n\n"
        except Exception as exc:  # pragma: no cover - defensive path
            log_event(
                logger,
                logging.ERROR,
                event="session.subagent_stream.failed",
                message="Unexpected session subagent stream failure",
                payload={"session_id": session_id, "event_count": event_count},
                exc_info=exc,
            )
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.delete("/{session_id}/subagents/{instance_id}")
async def delete_session_subagent(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        await call_maybe_async(
            service.delete_normal_mode_subagent,
            session_id,
            instance_id,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Subagent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/agents/{instance_id}/reflection")
async def get_agent_reflection(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await _call_session_read(
            "sessions.agent_reflection",
            service.get_agent_reflection,
            session_id,
            instance_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc


@router.post("/{session_id}/agents/{instance_id}/reflection:refresh")
async def refresh_agent_reflection(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await service.refresh_subagent_reflection(session_id, instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{session_id}/agents/{instance_id}/reflection")
async def update_agent_reflection(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    req: UpdateAgentReflectionRequest,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:

        def _update_agent_reflection() -> dict[str, object]:
            return service.update_agent_reflection(
                session_id,
                instance_id,
                summary=req.summary,
            )

        return await call_maybe_async(_update_agent_reflection)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{session_id}/agents/{instance_id}/reflection")
async def delete_agent_reflection(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await call_maybe_async(
            service.delete_agent_reflection,
            session_id,
            instance_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await _call_session_read(
        "sessions.events",
        service.get_global_events,
        session_id,
    )


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await _call_session_read(
        "sessions.messages",
        service.get_session_messages,
        session_id,
    )


@router.get("/{session_id}/agents/{instance_id}/messages")
async def get_agent_messages(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await _call_session_read(
        "sessions.agent_messages",
        service.get_agent_messages,
        session_id,
        instance_id,
    )


@router.get("/{session_id}/tasks")
async def get_session_tasks(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await _call_session_read(
        "sessions.tasks",
        service.get_session_tasks,
        session_id,
    )


@router.get("/{session_id}/token-usage")
async def get_session_token_usage(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    summary = await _call_session_read(
        "sessions.token_usage",
        service.get_token_usage_by_session,
        session_id,
    )
    return {
        "session_id": summary.session_id,
        "total_input_tokens": summary.total_input_tokens,
        "total_cached_input_tokens": summary.total_cached_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_reasoning_output_tokens": summary.total_reasoning_output_tokens,
        "total_tokens": summary.total_tokens,
        "total_requests": summary.total_requests,
        "total_tool_calls": summary.total_tool_calls,
        "by_role": {
            role_id: {
                "role_id": agent.role_id,
                "input_tokens": agent.input_tokens,
                "latest_input_tokens": agent.latest_input_tokens,
                "cached_input_tokens": agent.cached_input_tokens,
                "max_input_tokens": agent.max_input_tokens,
                "output_tokens": agent.output_tokens,
                "reasoning_output_tokens": agent.reasoning_output_tokens,
                "total_tokens": agent.total_tokens,
                "requests": agent.requests,
                "tool_calls": agent.tool_calls,
                "context_window": agent.context_window,
                "model_profile": agent.model_profile,
            }
            for role_id, agent in summary.by_role.items()
        },
    }


@router.get("/{session_id}/runs/{run_id}/token-usage")
async def get_run_token_usage(
    session_id: RequiredIdentifierStr,
    run_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    _ = session_id
    usage = await _call_session_read(
        "sessions.run_token_usage",
        service.get_token_usage_by_run,
        run_id,
    )
    return {
        "run_id": usage.run_id,
        "total_input_tokens": usage.total_input_tokens,
        "total_cached_input_tokens": usage.total_cached_input_tokens,
        "total_output_tokens": usage.total_output_tokens,
        "total_reasoning_output_tokens": usage.total_reasoning_output_tokens,
        "total_tokens": usage.total_tokens,
        "total_requests": usage.total_requests,
        "total_tool_calls": usage.total_tool_calls,
        "by_agent": [
            {
                "instance_id": a.instance_id,
                "role_id": a.role_id,
                "input_tokens": a.input_tokens,
                "latest_input_tokens": a.latest_input_tokens,
                "cached_input_tokens": a.cached_input_tokens,
                "max_input_tokens": a.max_input_tokens,
                "output_tokens": a.output_tokens,
                "reasoning_output_tokens": a.reasoning_output_tokens,
                "total_tokens": a.total_tokens,
                "requests": a.requests,
                "tool_calls": a.tool_calls,
                "context_window": a.context_window,
                "model_profile": a.model_profile,
            }
            for a in usage.by_agent
        ],
    }
