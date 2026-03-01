import json
import logging
from typing import Generator
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.core.enums import ExecutionMode
from agent_teams.core.models import IntentInput, SessionRecord

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/session", tags=["Sessions"])

def get_sdk(request: Request) -> AgentTeamsApp:
    return request.app.state.sdk

class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    metadata: dict[str, str] | None = None

class UpdateSessionRequest(BaseModel):
    metadata: dict[str, str]

class IntentRequest(BaseModel):
    intent: str
    parent_instruction: str | None = None
    execution_mode: ExecutionMode = ExecutionMode.AI
    confirmation_gate: bool = False


class GateResolveRequest(BaseModel):
    action: str          # 'approve' | 'revise'
    feedback: str = ''


class DispatchTaskRequest(BaseModel):
    task_id: str

@router.post("/", response_model=SessionRecord)
def create_session(req: CreateSessionRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.create_session(session_id=req.session_id, metadata=req.metadata)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=list[SessionRecord])
def list_sessions(sdk: AgentTeamsApp = Depends(get_sdk)):
    return list(sdk.list_sessions())

@router.get("/{session_id}", response_model=SessionRecord)
def get_session(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

@router.put("/{session_id}")
def update_session(session_id: str, req: UpdateSessionRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        sdk.update_session(session_id, req.metadata)
        return {"status": "success"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

@router.delete("/{session_id}")
def delete_session(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        sdk.delete_session(session_id)
        return {"status": "success"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

@router.get("/{session_id}/rounds")
def get_session_rounds(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_session_rounds(session_id)

@router.get("/{session_id}/rounds/{run_id}")
def get_session_round(session_id: str, run_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.get_round(session_id, run_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/{session_id}/agents")
def list_session_agents(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    agents = sdk.list_agents_in_session(session_id)
    return [agent.model_dump() for agent in agents]

@router.get("/{session_id}/events")
def get_session_events(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_global_events(session_id)

@router.get("/{session_id}/messages")
def get_session_all_messages(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_session_messages(session_id)

@router.get("/{session_id}/agents/{instance_id}/messages")
def get_agent_messages(session_id: str, instance_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_agent_messages(instance_id)

@router.get("/{session_id}/workflows")
def get_session_workflows(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_session_workflows(session_id)

@router.post("/{session_id}/intent")
def run_intent(session_id: str, req: IntentRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    input_event = IntentInput(
        session_id=session_id,
        intent=req.intent,
        parent_instruction=req.parent_instruction,
        execution_mode=req.execution_mode,
        confirmation_gate=req.confirmation_gate,
    )
    result = sdk.run_intent(input_event)
    return result.model_dump()

@router.get("/{session_id}/intent/stream")
def run_intent_stream(
    session_id: str,
    intent: str,
    execution_mode: ExecutionMode = ExecutionMode.AI,
    confirmation_gate: bool = False,
    sdk: AgentTeamsApp = Depends(get_sdk),
):
    input_event = IntentInput(
        session_id=session_id,
        intent=intent,
        execution_mode=execution_mode,
        confirmation_gate=confirmation_gate,
    )
    
    def event_generator() -> Generator[str, None, None]:
        try:
            for event in sdk.run_intent_stream(input_event):
                data_str = event.model_dump_json()
                yield f"data: {data_str}\n\n"
        except Exception as e:
            logger.exception("Error during event stream")
            err_data = json.dumps({"error": str(e)})
            yield f"data: {err_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ── Confirmation Gate ─────────────────────────────────────────────────────────

@router.get("/{session_id}/runs/{run_id}/gates")
def list_open_gates(session_id: str, run_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    """Return all currently open confirmation gates for a run."""
    return sdk.list_open_gates(run_id)


@router.post("/{session_id}/runs/{run_id}/gates/{task_id}/resolve")
def resolve_gate(
    session_id: str,
    run_id: str,
    task_id: str,
    req: GateResolveRequest,
    sdk: AgentTeamsApp = Depends(get_sdk),
):
    """Resolve a confirmation gate (approve or revise)."""
    try:
        sdk.resolve_gate(run_id=run_id, task_id=task_id, action=req.action, feedback=req.feedback)
        return {'status': 'ok', 'action': req.action}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Human Orchestration Mode ──────────────────────────────────────────────────

@router.post("/{session_id}/runs/{run_id}/dispatch")
def dispatch_human_task(
    session_id: str,
    run_id: str,
    req: DispatchTaskRequest,
    sdk: AgentTeamsApp = Depends(get_sdk),
):
    """
    Human mode: tell the coordinator which pending sub-task to execute next.
    Looks up the coordinator instance for this session automatically.
    """
    try:
        coordinator_instance_id = sdk._agent_repo.get_coordinator_instance_id(session_id)
        if coordinator_instance_id is None:
            raise HTTPException(status_code=404, detail='No coordinator instance found for session')
        sdk.dispatch_task_human(
            run_id=run_id,
            task_id=req.task_id,
            coordinator_instance_id=coordinator_instance_id,
        )
        return {'status': 'ok', 'dispatched_task_id': req.task_id}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
