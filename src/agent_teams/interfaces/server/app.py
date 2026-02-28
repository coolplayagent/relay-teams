import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_teams.core.config import load_runtime_config
from agent_teams.core.enums import RunEventType
from agent_teams.core.models import IntentInput, SessionRecord, TaskRecord
from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.roles.registry import RoleLoader
from agent_teams.tools.defaults import build_default_registry

logger = logging.getLogger(__name__)


def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent


DEFAULT_CONFIG_DIR = _get_project_root() / ".agent_teams"

# Pydantic models for incoming requests
class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    metadata: dict[str, str] | None = None

class UpdateSessionRequest(BaseModel):
    metadata: dict[str, str]

class IntentRequest(BaseModel):
    intent: str
    parent_instruction: str | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the SDK App globally
    # It wraps SQLite with thread-local connections via sqlite3 or loads pools, 
    # but we will instantiate it here for the app state.
    app.state.sdk = AgentTeamsApp(config_dir=DEFAULT_CONFIG_DIR, debug=True)
    yield
    # Cleanup if needed

app = FastAPI(
    title="Agent Teams Server",
    description="REST and Streaming API for Agent Teams orchestration.",
    version="0.1.0",
    lifespan=lifespan
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

def get_sdk(request: Request) -> AgentTeamsApp:
    return request.app.state.sdk

# ---------------------------------------------------------
# 1. Global / Config APIs
# ---------------------------------------------------------

@app.get("/global/health")
def health_check():
    return {"status": "ok", "version": app.version}

@app.get("/global/config")
def get_global_config():
    try:
        config = load_runtime_config(config_dir=DEFAULT_CONFIG_DIR)
        return config.model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------------------------------------------------
# 2. Session APIs
# ---------------------------------------------------------

@app.post("/session", response_model=SessionRecord)
def create_session(req: CreateSessionRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.create_session(session_id=req.session_id, metadata=req.metadata)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/session", response_model=list[SessionRecord])
def list_sessions(sdk: AgentTeamsApp = Depends(get_sdk)):
    return list(sdk.list_sessions())

@app.get("/session/{session_id}", response_model=SessionRecord)
def get_session(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.get_session(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

@app.put("/session/{session_id}")
def update_session(session_id: str, req: UpdateSessionRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        sdk.update_session(session_id, req.metadata)
        return {"status": "success"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

@app.get("/session/{session_id}/agents")
def list_session_agents(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    agents = sdk.list_agents_in_session(session_id)
    return [agent.model_dump() for agent in agents]

@app.get("/session/{session_id}/events")
def get_session_events(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_global_events(session_id)

@app.get("/session/{session_id}/messages")
def get_session_all_messages(session_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_session_messages(session_id)

@app.get("/session/{session_id}/agents/{instance_id}/messages")
def get_agent_messages(session_id: str, instance_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    return sdk.get_agent_messages(instance_id)

# ---------------------------------------------------------
# 3. Tasks APIs
# ---------------------------------------------------------

@app.get("/tasks", response_model=list[TaskRecord])
def list_tasks(sdk: AgentTeamsApp = Depends(get_sdk)):
    return list(sdk.list_tasks())

@app.get("/tasks/{task_id}", response_model=TaskRecord)
def get_task(task_id: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    try:
        return sdk.query_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

# ---------------------------------------------------------
# 4. Roles & Capabilities APIs
# ---------------------------------------------------------

@app.get("/roles")
def list_roles(sdk: AgentTeamsApp = Depends(get_sdk)):
    roles = sdk.list_roles()
    return [role.model_dump() for role in roles]

@app.post("/roles/validate")
def validate_roles():
    config = load_runtime_config(config_dir=DEFAULT_CONFIG_DIR)
    registry = RoleLoader().load_all(config.paths.roles_dir)
    tool_registry = build_default_registry()
    
    for role in registry.list_roles():
        tool_registry.validate_known(role.tools)
        
    loaded_count = len(registry.list_roles())
    return {"valid": True, "loaded_count": loaded_count}

# ---------------------------------------------------------
# 5. Intent & Execution APIs
# ---------------------------------------------------------

@app.post("/session/{session_id}/intent")
def run_intent(session_id: str, req: IntentRequest, sdk: AgentTeamsApp = Depends(get_sdk)):
    input_event = IntentInput(
        session_id=session_id, 
        intent=req.intent, 
        parent_instruction=req.parent_instruction
    )
    result = sdk.run_intent(input_event)
    return result.model_dump()

@app.get("/session/{session_id}/intent/stream")
async def run_intent_stream(session_id: str, intent: str, sdk: AgentTeamsApp = Depends(get_sdk)):
    """
    Server-Sent Events (SSE) endpoint for intent streaming.
    """
    input_event = IntentInput(session_id=session_id, intent=intent)
    
    async def event_generator() -> AsyncGenerator[str, None]:
        # Wrap the synchronous generator in an async compatible way if needed
        # SDK yields RunEvent directly. We will serialize them as SSE data bytes.
        try:
            for event in sdk.run_intent_stream(input_event):
                # Standard SSE format: 
                # data: {"key": "value"}
                # \n\n
                data_str = event.model_dump_json()
                yield f"data: {data_str}\n\n"
        except Exception as e:
            logger.exception("Error during event stream")
            err_data = json.dumps({"error": str(e)})
            yield f"data: {err_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
