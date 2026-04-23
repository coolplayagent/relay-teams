from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.concurrency import run_in_threadpool

from relay_teams.interfaces.server.deps import get_session_service
from relay_teams.interfaces.server.router_error_mapping import http_exception_for
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

        return await run_in_threadpool(_create_session)
    except (SystemRolesUnavailableError, ValueError) as exc:
        raise http_exception_for(
            exc,
            mappings=((SystemRolesUnavailableError, 503), (ValueError, 422)),
        ) from exc


@router.get("", response_model=list[SessionRecord])
async def list_sessions(
    service: SessionService = Depends(get_session_service),
) -> list[SessionRecord]:
    return list(service.list_sessions())


@router.get("/{session_id}", response_model=SessionRecord)
async def get_session(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return service.get_session(session_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc


@router.patch("/{session_id}")
async def update_session(
    session_id: RequiredIdentifierStr,
    req: SessionMetadataPatch,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        service.update_session(session_id, req)
        return {"status": "ok"}
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc
    except ValueError as exc:
        raise http_exception_for(exc, mappings=((ValueError, 422),)) from exc


@router.patch("/{session_id}/topology", response_model=SessionRecord)
async def update_session_topology(
    session_id: RequiredIdentifierStr,
    req: UpdateSessionTopologyRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return service.update_session_topology(
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
        service.delete_session(
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
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    return service.get_session_rounds(
        session_id,
        limit=limit,
        cursor_run_id=cursor_run_id,
    )


@router.get("/{session_id}/recovery")
async def get_session_recovery(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return service.get_recovery_snapshot(session_id)
    except KeyError as exc:
        raise http_exception_for(exc, key_error_detail="Session not found") from exc


@router.get("/{session_id}/rounds/{run_id}")
async def get_round(
    session_id: RequiredIdentifierStr,
    run_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return service.get_round(session_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/agents")
async def list_session_agents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        return list(service.list_agents_in_session(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/subagents")
async def list_session_subagents(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    try:
        return list(service.list_normal_mode_subagents(session_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.delete("/{session_id}/subagents/{instance_id}")
async def delete_session_subagent(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        service.delete_normal_mode_subagent(session_id, instance_id)
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
        return service.get_agent_reflection(session_id, instance_id)
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
        return service.update_agent_reflection(
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
        return service.delete_agent_reflection(session_id, instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Agent not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_global_events(session_id)


@router.get("/{session_id}/messages")
async def get_session_messages(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_session_messages(session_id)


@router.get("/{session_id}/agents/{instance_id}/messages")
async def get_agent_messages(
    session_id: RequiredIdentifierStr,
    instance_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_agent_messages(session_id, instance_id)


@router.get("/{session_id}/tasks")
async def get_session_tasks(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_session_tasks(session_id)


@router.get("/{session_id}/token-usage")
async def get_session_token_usage(
    session_id: RequiredIdentifierStr,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    summary = service.get_token_usage_by_session(session_id)
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
                "cached_input_tokens": agent.cached_input_tokens,
                "output_tokens": agent.output_tokens,
                "reasoning_output_tokens": agent.reasoning_output_tokens,
                "total_tokens": agent.total_tokens,
                "requests": agent.requests,
                "tool_calls": agent.tool_calls,
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
    usage = service.get_token_usage_by_run(run_id)
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
                "cached_input_tokens": a.cached_input_tokens,
                "output_tokens": a.output_tokens,
                "reasoning_output_tokens": a.reasoning_output_tokens,
                "total_tokens": a.total_tokens,
                "requests": a.requests,
                "tool_calls": a.tool_calls,
            }
            for a in usage.by_agent
        ],
    }
