from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator


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


@router.post("", response_model=SessionRecord)
async def create_session(
    req: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return await service.create_session_async(
            session_id=req.session_id,
            workspace_id=req.workspace_id,
            metadata=None if req.metadata is None else req.metadata.to_metadata_dict(),
        )
    except (SystemRolesUnavailableError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503), (ValueError, 422)),
        ) from exc


@router.get("", response_model=list[SessionRecord])
async def list_sessions(
    service: SessionService = Depends(get_session_service),
) -> list[SessionRecord]:
    records = await service.list_sessions_async()
    return list(records)


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return await service.get_session_async(session_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc


@router.patch("/{session_id}")
async def update_session(
    session_id: RequiredIdentifierStr,
    req: SessionMetadataPatch,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        await service.update_session_async(session_id, req)
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
    marker_task = asyncio.create_task(
        service.mark_latest_terminal_run_viewed_async(session_id)
    )
    try:
        await asyncio.wait_for(
            asyncio.shield(marker_task),
            timeout=SESSION_TERMINAL_VIEW_TIMEOUT_SECONDS,
        )
        return {"status": "ok"}
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except TimeoutError:
        _observe_deferred_terminal_view_result(marker_task, session_id)
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
    except asyncio.CancelledError:
        _observe_deferred_terminal_view_result(marker_task, session_id)
        raise


def _observe_deferred_terminal_view_result(
    marker_task: asyncio.Task[None],
    session_id: str,
) -> None:
    marker_task.add_done_callback(
        lambda task: _log_deferred_terminal_view_result(task, session_id)
    )


def _log_deferred_terminal_view_result(
    marker_task: asyncio.Task[None],
    session_id: str,
) -> None:
    try:
        marker_task.result()
    except asyncio.CancelledError:
        log_event(
            logger,
            logging.INFO,
            event="session.terminal_view.deferred_cancelled",
            message="Deferred session terminal view marker was cancelled",
            payload={"session_id": session_id},
        )
    except KeyError as exc:
        log_event(
            logger,
            logging.WARNING,
            event="session.terminal_view.deferred_missing",
            message="Deferred session terminal view marker found no session",
            payload={"session_id": session_id},
            exc_info=exc,
        )
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            event="session.terminal_view.deferred_failed",
            message="Deferred session terminal view marker failed",
            payload={"session_id": session_id},
            exc_info=exc,
        )


@router.patch("/{session_id}/topology", response_model=SessionRecord)
async def update_session_topology(
    session_id: RequiredIdentifierStr,
    req: UpdateSessionTopologyRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return await service.update_session_topology_async(
            session_id,
            session_mode=req.session_mode,
            normal_root_role_id=req.normal_root_role_id,
            orchestration_preset_id=req.orchestration_preset_id,
        )
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
        await service.delete_session_async(
            session_id,
            force=req.force if req is not None else False,
            cascade=req.cascade if req is not None else False,
        )
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
    summary: bool = False,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    return await service.get_session_rounds_async(
        session_id,
        limit=limit,
        cursor_run_id=cursor_run_id,
        timeline=timeline,
        summary=summary,
    )


@router.get("/{session_id}/recovery")
async def get_session_recovery(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await asyncio.wait_for(
            service.get_recovery_snapshot_async(session_id),
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
        return await service.get_round_async(session_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/agents")
async def list_session_agents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        agents = await service.list_agents_in_session_async(session_id)
        return list(agents)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/subagents")
async def list_session_subagents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        subagents = await service.list_normal_mode_subagents_async(session_id)
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
        await service.delete_normal_mode_subagent_async(session_id, instance_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Subagent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await service.get_global_events_async(session_id)


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await service.get_session_messages_async(session_id)


@router.get("/{session_id}/agents/{instance_id}/messages")
async def get_agent_messages(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await service.get_agent_messages_async(session_id, instance_id)


@router.get("/{session_id}/tasks")
async def get_session_tasks(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return await service.get_session_tasks_async(session_id)


@router.get("/{session_id}/token-usage")
async def get_session_token_usage(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    summary = await service.get_token_usage_by_session_async(session_id)
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
    usage = await service.get_token_usage_by_run_async(run_id)
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
