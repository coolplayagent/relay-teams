# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi import Request
from fastapi.testclient import TestClient
from typing import cast
from starlette.types import Message

from relay_teams.interfaces.server.deps import (
    get_realtime_stt_proxy_service,
    get_websocket_realtime_stt_proxy_service,
)
from relay_teams.interfaces.server.routers.speech import router
from relay_teams.speech import SpeechConfigUpdate


class _FakeSpeechConfigService:
    def __init__(self, *, fail_save: bool = False) -> None:
        self.fail_save = fail_save
        self.saved_config: SpeechConfigUpdate | None = None

    def get_config_payload(self) -> dict[str, object]:
        return {
            "configured": self.saved_config is not None,
            "stt_profile_name": self.saved_config.stt_profile_name
            if self.saved_config
            else None,
        }

    def save_config(self, config: SpeechConfigUpdate) -> None:
        if self.fail_save:
            raise ValueError("bad profile")
        self.saved_config = config

    async def get_config_payload_async(self) -> dict[str, object]:
        return self.get_config_payload()

    async def save_config_async(self, config: SpeechConfigUpdate) -> None:
        self.save_config(config)


class _FakeRealtimeSttProxyService:
    def __init__(self) -> None:
        self.handled = False

    async def handle_client(self, websocket: WebSocket) -> None:
        self.handled = True
        await websocket.accept()
        await websocket.send_json({"type": "status", "status": "ready"})
        await websocket.close()


class _FakeContainer:
    def __init__(
        self,
        realtime_stt_proxy_service: _FakeRealtimeSttProxyService,
        speech_config_service: _FakeSpeechConfigService | None = None,
    ) -> None:
        self.realtime_stt_proxy_service = realtime_stt_proxy_service
        self.speech_config_service = speech_config_service or _FakeSpeechConfigService()


def test_stt_websocket_uses_websocket_container_dependency() -> None:
    service = _FakeRealtimeSttProxyService()
    app = FastAPI()
    app.state.container = _FakeContainer(service)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    with client.websocket_connect("/api/speech/stt/stream") as websocket:
        assert websocket.receive_json() == {"type": "status", "status": "ready"}

    assert service.handled is True


def test_speech_dependencies_resolve_http_and_websocket_containers() -> None:
    service = _FakeRealtimeSttProxyService()
    app = FastAPI()
    app.state.container = _FakeContainer(service)

    async def receive() -> Message:
        return {"type": "websocket.disconnect"}

    async def send(message: Message) -> None:
        return

    request_scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "app": app,
    }
    websocket_scope = {
        "type": "websocket",
        "path": "/ws",
        "headers": [],
        "app": app,
    }

    assert get_realtime_stt_proxy_service(Request(request_scope)) is service
    assert (
        get_websocket_realtime_stt_proxy_service(
            cast(WebSocket, WebSocket(websocket_scope, receive=receive, send=send))
        )
        is service
    )


def test_speech_config_routes_read_and_save_config() -> None:
    realtime_service = _FakeRealtimeSttProxyService()
    speech_service = _FakeSpeechConfigService()
    app = FastAPI()
    app.state.container = _FakeContainer(realtime_service, speech_service)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    assert client.get("/api/speech/config").json() == {
        "configured": False,
        "stt_profile_name": None,
    }

    response = client.put(
        "/api/speech/config",
        json={"stt_profile_name": "stt", "language": "zh-CN"},
    )

    assert response.status_code == 200
    assert response.json() == {"configured": True, "stt_profile_name": "stt"}
    assert speech_service.saved_config is not None
    assert speech_service.saved_config.language == "zh-CN"


def test_speech_config_route_returns_bad_request_for_invalid_config() -> None:
    app = FastAPI()
    app.state.container = _FakeContainer(
        _FakeRealtimeSttProxyService(),
        _FakeSpeechConfigService(fail_save=True),
    )
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    response = client.put("/api/speech/config", json={"stt_profile_name": "missing"})

    assert response.status_code == 400
    assert response.json() == {"detail": "bad profile"}
