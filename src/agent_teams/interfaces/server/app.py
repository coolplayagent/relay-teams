# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
import logging
import re
import signal
import time
from types import FrameType

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from agent_teams.builtin import ensure_app_config_bootstrap
from agent_teams.interfaces.server.config_paths import get_frontend_dist_dir
from agent_teams.interfaces.server.container import ServerContainer
from agent_teams.interfaces.server.routers import (
    logs,
    mcp,
    prompts,
    reflection,
    roles,
    runs,
    sessions,
    system,
    tasks,
    triggers,
)
from agent_teams.logger import (
    configure_logging,
    get_logger,
    log_event,
    shutdown_logging,
)
from agent_teams.paths import get_app_config_dir
from agent_teams.trace import bind_trace_context, generate_request_id

logger = get_logger(__name__)
FRONTEND_DIST_DIR = get_frontend_dist_dir()
RequestHandler = Callable[[Request], Awaitable[Response]]
SignalHandler = Callable[[int, FrameType | None], None]
SignalHandlerRef = int | SignalHandler | None
_SUPPRESSED_SUCCESS_PATHS = (
    re.compile(r"^/api/system/health$"),
    re.compile(r"^/api/sessions/[^/]+/recovery$"),
    re.compile(r"^/api/sessions/[^/]+/runs/[^/]+/token-usage$"),
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config_dir = get_app_config_dir()
    ensure_app_config_bootstrap(config_dir)
    configure_logging(config_dir=config_dir)
    _register_signal_handlers()
    app.state.container = ServerContainer(config_dir=config_dir)
    await app.state.container.start()
    log_event(
        logger,
        logging.INFO,
        event="app.startup",
        message="Agent Teams server started",
        payload={"config_dir": str(config_dir)},
    )
    yield
    await app.state.container.stop()
    log_event(
        logger,
        logging.INFO,
        event="app.shutdown",
        message="Agent Teams server stopped",
    )
    shutdown_logging()


app = FastAPI(
    title="Agent Teams Server",
    description="REST API for Agent Teams orchestration.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(system.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(tasks.router, prefix="/api")
app.include_router(roles.router, prefix="/api")
app.include_router(prompts.router, prefix="/api")
app.include_router(reflection.router, prefix="/api")
app.include_router(logs.router, prefix="/api")
app.include_router(triggers.router, prefix="/api")


@app.middleware("http")
async def tracing_middleware(request: Request, call_next: RequestHandler) -> Response:
    request_id = request.headers.get("X-Request-Id") or generate_request_id()
    trace_id = request.headers.get("X-Trace-Id") or request_id
    started = time.perf_counter()
    path = request.url.path

    with bind_trace_context(request_id=request_id, trace_id=trace_id):
        response: Response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        log_level = _resolve_request_log_level(
            path=path, status_code=response.status_code
        )
        if log_level is not None:
            log_event(
                logger,
                log_level,
                event="http.request.completed",
                message="HTTP request completed",
                duration_ms=elapsed_ms,
                payload={
                    "method": request.method,
                    "path": path,
                    "status_code": response.status_code,
                },
            )
        return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log_event(
        logger,
        logging.ERROR,
        event="http.request.failed",
        message="Unhandled server exception",
        payload={"method": request.method, "path": request.url.path},
        exc_info=exc,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _resolve_request_log_level(*, path: str, status_code: int) -> int | None:
    if status_code >= 500:
        return logging.ERROR
    if status_code >= 400:
        return logging.WARNING
    if _is_suppressed_success_path(path):
        return None
    return logging.DEBUG


def _is_suppressed_success_path(path: str) -> bool:
    return any(pattern.match(path) is not None for pattern in _SUPPRESSED_SUCCESS_PATHS)


def _register_signal_handlers() -> None:
    registered_signals = (signal.SIGTERM, signal.SIGINT)
    previous_handlers: dict[int, SignalHandlerRef] = {
        sig: signal.getsignal(sig) for sig in registered_signals
    }

    def _forward_to_previous_handler(
        sig: int,
        frame: FrameType | None,
        previous_handler: SignalHandlerRef,
    ) -> None:
        if previous_handler is None or previous_handler == signal.SIG_IGN:
            return
        if callable(previous_handler):
            previous_handler(sig, frame)
            return
        if previous_handler == signal.SIG_DFL:
            if sig == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + sig)

    def _on_signal(sig: int, frame: FrameType | None) -> None:
        signame = signal.Signals(sig).name
        log_event(
            logger,
            logging.WARNING,
            event="process.signal.received",
            message="Shutdown signal received",
            payload={"signal": signame},
        )
        previous_handler = previous_handlers.get(sig)
        _forward_to_previous_handler(sig, frame, previous_handler)

    for sig in registered_signals:
        _ = signal.signal(sig, _on_signal)


if FRONTEND_DIST_DIR.exists():
    app.mount(
        "/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend"
    )
else:

    @app.get("/")
    def missing_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "status": "frontend_not_built",
                "message": "Frontend build artifacts were not found in ./frontend/dist",
            }
        )
