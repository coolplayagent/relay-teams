from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from fastapi import APIRouter, Depends, HTTPException

from agent_teams.interfaces.server.deps import get_session_service
from agent_teams.sessions import SessionService
from agent_teams.sessions.session_models import SessionRecord

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
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    return service.create_session(session_id=req.session_id, metadata=req.metadata)


@router.get("", response_model=list[SessionRecord])
def list_sessions(
    service: SessionService = Depends(get_session_service),
) -> list[SessionRecord]:
    return list(service.list_sessions())


@router.get("/{session_id}", response_model=SessionRecord)
def get_session(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> SessionRecord:
    try:
        return service.get_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    req: UpdateSessionRequest,
    service: SessionService = Depends(get_session_service),
) -> dict[str, str]:
    try:
        service.update_session(session_id, req.metadata)
        return {"status": "ok"}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    service: SessionService = Depends(get_session_service),
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
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    return service.get_session_rounds(
        session_id,
        limit=limit,
        cursor_run_id=cursor_run_id,
    )


@router.get("/{session_id}/recovery")
def get_session_recovery(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return service.get_recovery_snapshot(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Session not found") from exc


@router.get("/{session_id}/rounds/{run_id}")
def get_round(
    session_id: str,
    run_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    try:
        return service.get_round(session_id, run_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{session_id}/agents")
def list_session_agents(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return [
        record.model_dump() for record in service.list_agents_in_session(session_id)
    ]


@router.get("/{session_id}/events")
def get_session_events(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_global_events(session_id)


@router.get("/{session_id}/messages")
def get_session_messages(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_session_messages(session_id)


@router.get("/{session_id}/agents/{instance_id}/messages")
def get_agent_messages(
    session_id: str,
    instance_id: str,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_agent_messages(session_id, instance_id)


@router.get("/{session_id}/tasks")
def get_session_tasks(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> list[dict[str, object]]:
    return service.get_session_tasks(session_id)


@router.get("/{session_id}/token-usage")
def get_session_token_usage(
    session_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    summary = service.get_token_usage_by_session(session_id)
    return {
        "session_id": summary.session_id,
        "total_input_tokens": summary.total_input_tokens,
        "total_output_tokens": summary.total_output_tokens,
        "total_tokens": summary.total_tokens,
        "total_requests": summary.total_requests,
        "total_tool_calls": summary.total_tool_calls,
        "by_role": {
            role_id: {
                "role_id": agent.role_id,
                "input_tokens": agent.input_tokens,
                "output_tokens": agent.output_tokens,
                "total_tokens": agent.total_tokens,
                "requests": agent.requests,
                "tool_calls": agent.tool_calls,
            }
            for role_id, agent in summary.by_role.items()
        },
    }


@router.get("/{session_id}/runs/{run_id}/token-usage")
def get_run_token_usage(
    session_id: str,
    run_id: str,
    service: SessionService = Depends(get_session_service),
) -> dict[str, object]:
    usage = service.get_token_usage_by_run(run_id)
    return {
        "run_id": usage.run_id,
        "total_input_tokens": usage.total_input_tokens,
        "total_output_tokens": usage.total_output_tokens,
        "total_tokens": usage.total_tokens,
        "total_requests": usage.total_requests,
        "total_tool_calls": usage.total_tool_calls,
        "by_agent": [
            {
                "instance_id": a.instance_id,
                "role_id": a.role_id,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "total_tokens": a.total_tokens,
                "requests": a.requests,
                "tool_calls": a.tool_calls,
            }
            for a in usage.by_agent
        ],
    }
