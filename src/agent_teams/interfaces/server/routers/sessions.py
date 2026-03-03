from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException

from agent_teams.application.service import AgentTeamsService
from agent_teams.core.models import SessionRecord
from agent_teams.interfaces.server.deps import get_service

router = APIRouter(prefix="/sessions", tags=["Sessions"])


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    metadata: dict[str, str] | None = None


class UpdateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, str]


@router.post("", response_model=SessionRecord)
def create_session(
    req: CreateSessionRequest,
    service: AgentTeamsService = Depends(get_service),
) -> SessionRecord:
    return service.create_session(session_id=req.session_id, metadata=req.metadata)


@router.get("", response_model=list[SessionRecord])
def list_sessions(service: AgentTeamsService = Depends(get_service)) -> list[SessionRecord]:
    return list(service.list_sessions())


@router.get("/{session_id}", response_model=SessionRecord)
def get_session(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> SessionRecord:
    try:
        return service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    req: UpdateSessionRequest,
    service: AgentTeamsService = Depends(get_service),
) -> dict[str, str]:
    try:
        service.update_session(session_id, req.metadata)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> dict[str, str]:
    try:
        service.delete_session(session_id)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/rounds")
def get_session_rounds(
    session_id: str,
    limit: int = 8,
    cursor_run_id: str | None = None,
    service: AgentTeamsService = Depends(get_service),
) -> dict:
    return service.get_session_rounds(
        session_id,
        limit=limit,
        cursor_run_id=cursor_run_id,
    )


@router.get("/{session_id}/rounds/{run_id}")
def get_round(
    session_id: str,
    run_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> dict:
    try:
        return service.get_round(session_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/agents")
def list_session_agents(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> list[dict]:
    return [record.model_dump() for record in service.list_agents_in_session(session_id)]


@router.get("/{session_id}/events")
def get_session_events(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> list[dict]:
    return service.get_global_events(session_id)


@router.get("/{session_id}/messages")
def get_session_messages(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> list[dict]:
    return service.get_session_messages(session_id)


@router.get("/{session_id}/agents/{instance_id}/messages")
def get_agent_messages(
    session_id: str,
    instance_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> list[dict]:
    return service.get_agent_messages(session_id, instance_id)


@router.get("/{session_id}/workflows")
def get_session_workflows(
    session_id: str,
    service: AgentTeamsService = Depends(get_service),
) -> list[dict]:
    return service.get_session_workflows(session_id)
