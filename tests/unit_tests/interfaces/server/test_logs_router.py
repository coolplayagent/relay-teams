# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from relay_teams.interfaces.server.routers import logs
from relay_teams.logger import configure_logging, shutdown_logging


def test_frontend_logs_route_writes_frontend_log_only(tmp_path: Path) -> None:
    config_dir = tmp_path / ".agent_teams"
    app = FastAPI()
    app.include_router(logs.router, prefix="/api")
    client = TestClient(app)

    configure_logging(config_dir=config_dir)
    response = client.post(
        "/api/logs/frontend",
        json={
            "events": [
                {
                    "level": "error",
                    "event": "ui.failure",
                    "message": "frontend failed",
                    "trace_id": "trace-ui",
                    "request_id": "req-ui",
                    "run_id": "run-ui",
                    "session_id": "session-ui",
                    "page": "chat",
                    "route": "/chat",
                    "browser_session_id": "browser-1",
                    "user_agent": "pytest",
                    "payload": {"component": "composer"},
                }
            ]
        },
    )
    shutdown_logging()

    assert response.status_code == 200
    assert response.json() == {"accepted": 1}

    backend_text = (config_dir / "log" / "backend.log").read_text(encoding="utf-8")
    frontend_text = (config_dir / "log" / "frontend.log").read_text(encoding="utf-8")
    assert "event=frontend.ui.failure" not in backend_text
    assert "event=frontend.ui.failure" in frontend_text
    assert "browser_session_id" in frontend_text


def test_frontend_logs_route_runs_batch_in_threadpool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_in_threadpool(
        func: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> object:
        calls.append((func.__name__, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(logs, "call_maybe_async", fake_run_in_threadpool)
    app = FastAPI()
    app.include_router(logs.router, prefix="/api")
    client = TestClient(app)

    response = client.post(
        "/api/logs/frontend",
        json={
            "events": [
                {
                    "level": "info",
                    "event": "ui.ready",
                    "message": "frontend ready",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {"accepted": 1}
    assert [call[0] for call in calls] == ["_ingest_frontend_logs"]
