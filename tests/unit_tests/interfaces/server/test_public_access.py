# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.responses import Response

from relay_teams.interfaces.server.public_access import (
    is_local_hostname,
    is_public_access_guard_enabled,
    is_public_host_allowed_request,
    public_access_denied_detail,
    request_uses_public_host,
)


def test_local_hostname_detection_handles_loopback_and_test_hosts() -> None:
    assert is_local_hostname("127.0.0.1") is True
    assert is_local_hostname("localhost") is True
    assert is_local_hostname("testserver") is True
    assert is_local_hostname("192.168.1.8") is False
    assert is_local_hostname("agent-teams.example.com") is False


def test_public_host_requests_are_blocked_except_allowlist() -> None:
    app = FastAPI()

    @app.middleware("http")
    async def _public_host_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if (
            not is_public_access_guard_enabled()
            or not request_uses_public_host(request)
            or is_public_host_allowed_request(request)
        ):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={"detail": public_access_denied_detail()},
        )

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.post("/api/triggers/github/deliveries")
    def deliveries() -> dict[str, str]:
        return {"status": "accepted"}

    client = TestClient(app)

    blocked = client.get("/", headers={"host": "agent-teams.example.com"})
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": public_access_denied_detail()}

    health_response = client.get(
        "/api/system/health",
        headers={"host": "agent-teams.example.com"},
    )
    assert health_response.status_code == 200
    assert health_response.json() == {"status": "healthy"}

    delivery = client.post(
        "/api/triggers/github/deliveries",
        headers={"host": "agent-teams.example.com"},
    )
    assert delivery.status_code == 200
    assert delivery.json() == {"status": "accepted"}

    local = client.get("/", headers={"host": "127.0.0.1:8000"})
    assert local.status_code == 200
    assert local.json() == {"status": "ok"}
