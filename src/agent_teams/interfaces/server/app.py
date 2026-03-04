from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from pathlib import Path
import signal
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from agent_teams.application.service import AgentTeamsService
from agent_teams.interfaces.server.routers import logs, roles, runs, sessions, system, tasks
from agent_teams.runtime.logging import configure_logging, get_logger, log_event
from agent_teams.runtime.trace import bind_trace_context, generate_request_id

logger = get_logger(__name__)


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent.parent


DEFAULT_CONFIG_DIR = _get_project_root() / ".agent_teams"
FRONTEND_DIST_DIR = _get_project_root() / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(persist_db_path=DEFAULT_CONFIG_DIR / "agent_teams.db")
    _register_signal_handlers()
    app.state.service = AgentTeamsService(config_dir=DEFAULT_CONFIG_DIR, debug=False)
    log_event(logger, logging.INFO, event='app.startup', message='Agent Teams server started')
    yield
    log_event(logger, logging.INFO, event='app.shutdown', message='Agent Teams server stopped')


app = FastAPI(
    title="Agent Teams Server",
    description="REST API for Agent Teams orchestration.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(system.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(logs.router, prefix="/api")


@app.middleware('http')
async def tracing_middleware(request: Request, call_next):
    request_id = request.headers.get('X-Request-Id') or generate_request_id()
    trace_id = request.headers.get('X-Trace-Id') or request_id
    started = time.perf_counter()

    with bind_trace_context(request_id=request_id, trace_id=trace_id):
        log_event(
            logger,
            logging.INFO,
            event='http.request.received',
            message='Incoming HTTP request',
            payload={'method': request.method, 'path': request.url.path},
        )
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers['X-Request-Id'] = request_id
        response.headers['X-Trace-Id'] = trace_id
        log_event(
            logger,
            logging.INFO,
            event='http.request.completed',
            message='HTTP request completed',
            duration_ms=elapsed_ms,
            payload={
                'method': request.method,
                'path': request.url.path,
                'status_code': response.status_code,
            },
        )
        return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log_event(
        logger,
        logging.ERROR,
        event='http.request.failed',
        message='Unhandled server exception',
        payload={'method': request.method, 'path': request.url.path},
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={'detail': 'Internal server error'})


def _register_signal_handlers() -> None:
    def _on_signal(sig: int, _frame) -> None:
        signame = signal.Signals(sig).name
        log_event(
            logger,
            logging.WARNING,
            event='process.signal.received',
            message='Shutdown signal received',
            payload={'signal': signame},
        )
        logging.shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _on_signal)

if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
else:

    @app.get("/")
    def missing_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "status": "frontend_not_built",
                "message": "Frontend build artifacts were not found in ./frontend/dist",
            }
        )
