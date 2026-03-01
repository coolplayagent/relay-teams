import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from agent_teams.interfaces.sdk.client import AgentTeamsApp
from agent_teams.interfaces.server.routers import system, sessions, tasks, roles

logger = logging.getLogger(__name__)

def _get_project_root() -> Path:
    return Path(__file__).parent.parent.parent.parent.parent

DEFAULT_CONFIG_DIR = _get_project_root() / ".agent_teams"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the SDK App globally
    app.state.sdk = AgentTeamsApp(config_dir=DEFAULT_CONFIG_DIR, debug=True)
    yield
    # Cleanup if needed

app = FastAPI(
    title="Agent Teams Server",
    description="REST API for Agent Teams orchestration.",
    version="0.1.0",
    lifespan=lifespan
)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# Include JSON API Routers
app.include_router(system.router, prefix="/api/v1")
app.include_router(sessions.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(roles.router, prefix="/api/v1")

# Mount Static Assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def serve_index():
    return FileResponse(STATIC_DIR / "index.html")

def get_sdk(request: Request) -> AgentTeamsApp:
    return request.app.state.sdk
