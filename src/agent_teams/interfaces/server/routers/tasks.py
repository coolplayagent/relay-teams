from fastapi import APIRouter, Depends, HTTPException, Request
from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.core.models import TaskRecord

router = APIRouter(prefix="/tasks", tags=["Tasks"])

def get_sdk(request: Request) -> AgentTeamsApp:
    return request.app.state.sdk

@router.get("/", response_model=list[TaskRecord])
def list_tasks(sdk: AgentTeamsApp = Depends(get_sdk)):
    return list(sdk.list_tasks())

@router.get("/{task_id}", response_model=TaskRecord)
def get_task(task_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.query_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
