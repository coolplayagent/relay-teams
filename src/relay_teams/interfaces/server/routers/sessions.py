from __future__ import annotations

import asyncio
from datetime import date, datetime
import inspect
import json
import logging
import time
from threading import Lock
from typing import Protocol, cast

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator


from relay_teams.interfaces.server.async_call import (
    RouteWorkRejectedError,
    call_maybe_async_in_session_fast_read_thread,
    call_maybe_async_in_session_projection_refresh_thread,
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
TERMINAL_VIEW_DEFERRED_MIN_INTERVAL_SECONDS = 5.0
_terminal_view_pending_session_ids: set[str] = set()
_terminal_view_last_started_monotonic: dict[str, float] = {}
_terminal_view_pending_lock = Lock()


class _TerminalViewMarker(Protocol):
    async def assert_session_exists_async(self, session_id: str) -> None:
        raise NotImplementedError

    async def mark_latest_terminal_run_viewed_async(self, session_id: str) -> None:
        raise NotImplementedError

    def mark_latest_terminal_run_viewed(self, session_id: str) -> None:
        raise NotImplementedError


def _json_response(payload: object) -> Response:
    return Response(
        content=_json_text(payload),
        media_type="application/json",
    )


def _json_text(payload: object) -> str:  # pragma: no cover
    if isinstance(payload, BaseModel):
        return payload.model_dump_json()
    if isinstance(payload, tuple | list) and all(
        isinstance(item, BaseModel) for item in payload
    ):
        return "[" + ",".join(item.model_dump_json() for item in payload) + "]"
    return json.dumps(_json_content(payload), separators=(",", ":"))


def _json_content(value: object) -> object:  # pragma: no cover
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple | list):
        return [_json_content(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_content(item) for key, item in value.items()}
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


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
) -> Response:
    records = await service.list_sessions_async()
    return _json_response(records)


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    try:
        return _json_response(await service.get_session_async(session_id))
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
    if not _begin_deferred_terminal_view(session_id):
        return {"status": "deferred"}
    try:
        await call_maybe_async_in_session_fast_read_thread(
            "session.terminal_view.exists",
            service.assert_session_exists_async,
            session_id,
        )
    except KeyError as exc:
        _finish_deferred_terminal_view(session_id, clear_cooldown=True)
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except Exception:
        _finish_deferred_terminal_view(session_id, clear_cooldown=True)
        raise
    marker_task = asyncio.create_task(
        call_maybe_async_in_session_projection_refresh_thread(
            "session.terminal_view",
            service.mark_latest_terminal_run_viewed_async,
            session_id,
        )
    )
    _observe_deferred_terminal_view_result(marker_task, session_id, service)
    return {"status": "deferred"}


def _begin_deferred_terminal_view(session_id: str) -> bool:
    now = time.monotonic()
    with _terminal_view_pending_lock:
        if session_id in _terminal_view_pending_session_ids:
            return False
        last_started = _terminal_view_last_started_monotonic.get(session_id)
        if (
            last_started is not None
            and now - last_started < TERMINAL_VIEW_DEFERRED_MIN_INTERVAL_SECONDS
        ):
            return False
        _terminal_view_pending_session_ids.add(session_id)
        _terminal_view_last_started_monotonic[session_id] = now
        return True


def _finish_deferred_terminal_view(
    session_id: str,
    *,
    clear_cooldown: bool = False,
) -> None:
    with _terminal_view_pending_lock:
        _terminal_view_pending_session_ids.discard(session_id)
        if clear_cooldown:
            _terminal_view_last_started_monotonic.pop(session_id, None)


def _observe_deferred_terminal_view_result(
    marker_task: asyncio.Task[None],
    session_id: str,
    service: _TerminalViewMarker,
    *,
    attempt: int = 0,
) -> None:
    marker_task.add_done_callback(
        lambda task: _log_deferred_terminal_view_result(
            task,
            session_id,
            service=service,
            attempt=attempt,
        )
    )


def _log_deferred_terminal_view_result(  # pragma: no cover
    marker_task: asyncio.Task[None],
    session_id: str,
    service: _TerminalViewMarker | None = None,
    *,
    attempt: int = 0,
) -> None:
    try:
        marker_task.result()
    except RouteWorkRejectedError:
        _ = (service, attempt)
        log_event(
            logger,
            logging.WARNING,
            event="session.terminal_view.deferred_rejected",
            message="Deferred session terminal view marker was dropped under load",
            payload={"session_id": session_id},
        )
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
    finally:
        _finish_deferred_terminal_view(session_id)


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
async def get_session_rounds(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    limit: int = 8,
    cursor_run_id: OptionalIdentifierStr = None,
    timeline: bool = False,
    summary: bool = False,
    service: SessionService = Depends(get_session_service),
) -> Response:
    fast_snapshot = service.get_fast_cached_session_rounds_snapshot(
        session_id,
        limit=limit,
        cursor_run_id=cursor_run_id,
        timeline=timeline,
        summary=summary,
    )
    if fast_snapshot is not None:
        return _json_response(fast_snapshot)
    return _json_response(
        await call_maybe_async_in_session_fast_read_thread(
            "session.rounds",
            service.get_cached_session_rounds_async,
            session_id,
            limit=limit,
            cursor_run_id=cursor_run_id,
            timeline=timeline,
            summary=summary,
        )
    )


@router.get("/{session_id}/recovery")
async def get_session_recovery(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    try:
        fast_snapshot = service.get_fast_cached_recovery_snapshot(session_id)
        if fast_snapshot is not None:
            return _json_response(fast_snapshot)
        return _json_response(
            await call_maybe_async_in_session_fast_read_thread(
                "session.recovery",
                service.get_cached_recovery_snapshot_async,
                session_id,
            )
        )
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc


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
async def list_session_agents(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    try:
        fast_snapshot = service.get_fast_cached_agents_snapshot(session_id)
        if fast_snapshot is not None:
            fast_items = fast_snapshot.get("items")
            if isinstance(fast_items, list):
                return _json_response(
                    [item for item in fast_items if isinstance(item, dict)]
                )
            return _json_response([])
        agents = cast(
            tuple[dict[str, object], ...],
            await call_maybe_async_in_session_fast_read_thread(
                "session.agents",
                service.list_cached_agents_in_session_async,
                session_id,
            ),
        )
        return _json_response(agents)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/subagents")
async def list_session_subagents(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    force_refresh: bool = False,
    service: SessionService = Depends(get_session_service),
) -> Response:
    try:
        if force_refresh:
            subagents = cast(
                tuple[dict[str, object], ...],
                await call_maybe_async_in_session_fast_read_thread(
                    "session.subagents.force_refresh",
                    service.list_normal_mode_subagents_async,
                    session_id,
                ),
            )
            return _json_response(subagents)
        fast_snapshot = service.get_fast_cached_normal_mode_subagents_snapshot(
            session_id
        )
        if fast_snapshot is not None:
            fast_items = fast_snapshot.get("items")
            if isinstance(fast_items, list):
                return _json_response(
                    [item for item in fast_items if isinstance(item, dict)]
                )
            return _json_response([])
        subagents = cast(
            tuple[dict[str, object], ...],
            await call_maybe_async_in_session_fast_read_thread(
                "session.subagents",
                service.list_cached_normal_mode_subagents_async,
                session_id,
            ),
        )
        return _json_response(subagents)
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


@router.get("/{session_id}/agents/{instance_id}/reflection")
async def get_agent_reflection(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return await service.get_agent_reflection_async(session_id, instance_id)
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
        return await service.update_agent_reflection_async(
            session_id,
            instance_id,
            summary=req.summary,
        )
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
        return await service.delete_agent_reflection_async(session_id, instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
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
async def get_session_tasks(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    fast_snapshot = service.get_fast_cached_session_tasks_snapshot(session_id)
    if fast_snapshot is not None:
        fast_items = fast_snapshot.get("items")
        if isinstance(fast_items, list):
            return _json_response(
                [item for item in fast_items if isinstance(item, dict)]
            )
        return _json_response([])
    tasks_result: object = service.list_cached_session_tasks_async(session_id)
    if inspect.isawaitable(tasks_result):
        tasks_result = await tasks_result
    tasks = cast(tuple[dict[str, object], ...], tasks_result)
    return _json_response(tasks)


@router.get("/{session_id}/token-usage")
async def get_session_token_usage(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    fast_snapshot = service.get_fast_cached_token_usage_by_session_snapshot(session_id)
    if fast_snapshot is not None:
        return _json_response(fast_snapshot)
    snapshot_result: object = service.get_cached_token_usage_by_session_snapshot_async(
        session_id
    )
    if inspect.isawaitable(snapshot_result):
        snapshot_result = await snapshot_result
    return _json_response(cast(dict[str, object], snapshot_result))


@router.get("/{session_id}/runs/{run_id}/token-usage")
async def get_run_token_usage(  # pragma: no cover
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


@router.get("/{session_id}/subagents:snapshot")
async def list_session_subagents_snapshot(  # pragma: no cover
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> Response:
    try:
        return _json_response(
            await call_maybe_async_in_session_fast_read_thread(
                "session.subagents.snapshot",
                service.get_cached_normal_mode_subagents_snapshot_async,
                session_id,
            )
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc
