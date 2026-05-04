# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from relay_teams.interfaces.sdk.client import (
    AsyncAgentTeamsClient,
    RunHandle,
    SyncAgentTeamsClient,
)


class TestSyncAgentTeamsClient:
    def test_constructor_mirrors_async_client(self) -> None:
        client = SyncAgentTeamsClient(
            base_url="http://server.test",
            timeout_seconds=7.5,
            stream_timeout_seconds=120.0,
        )
        assert client._base_url == "http://server.test"
        assert client._timeout_seconds == 7.5
        assert client._stream_timeout_seconds == 120.0

    def test_health_delegates_to_async_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[dict[str, object]] = []

        async def fake_request_json(
            self_async: AsyncAgentTeamsClient,
            method: str,
            path: str,
            payload: object | None = None,
        ) -> dict[str, object]:
            captured.append({"method": method, "path": path, "payload": payload})
            return {"status": "ok"}

        monkeypatch.setattr(AsyncAgentTeamsClient, "_request_json", fake_request_json)

        client = SyncAgentTeamsClient(base_url="http://server.test")
        result = client.health()
        assert result == {"status": "ok"}
        assert captured == [
            {"method": "GET", "path": "/api/system/health", "payload": None}
        ]

    def test_create_run_returns_run_handle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_request_json(
            self_async: AsyncAgentTeamsClient,
            method: str,
            path: str,
            payload: object | None = None,
        ) -> dict[str, object]:
            return {"run_id": "run-1", "session_id": "session-1"}

        monkeypatch.setattr(AsyncAgentTeamsClient, "_request_json", fake_request_json)

        client = SyncAgentTeamsClient(base_url="http://server.test")
        handle = cast(
            RunHandle, client.create_run(input="hello", session_id="session-1")
        )
        assert handle.run_id == "run-1"
        assert handle.session_id == "session-1"

    def test_method_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_request_json(
            self_async: AsyncAgentTeamsClient,
            method: str,
            path: str,
            payload: object | None = None,
        ) -> dict[str, object]:
            raise RuntimeError("connection refused")

        monkeypatch.setattr(AsyncAgentTeamsClient, "_request_json", fake_request_json)

        client = SyncAgentTeamsClient(base_url="http://server.test")
        with pytest.raises(RuntimeError, match="connection refused"):
            client.health()

    def test_stream_run_events_returns_sync_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_stream(
            self_async: AsyncAgentTeamsClient, run_id: str
        ) -> AsyncIterator[dict[str, object]]:
            _ = run_id
            yield {"event": "started"}
            yield {"event": "completed"}

        monkeypatch.setattr(
            AsyncAgentTeamsClient,
            "stream_run_events",
            fake_stream,
        )

        client = SyncAgentTeamsClient(base_url="http://server.test")
        items = client.stream_run_events("run-1")
        assert isinstance(items, list)
        assert items == [{"event": "started"}, {"event": "completed"}]

    def test_repr(self) -> None:
        client = SyncAgentTeamsClient(
            base_url="http://server.test",
            timeout_seconds=7.5,
            stream_timeout_seconds=120.0,
        )
        rep = repr(client)
        assert "SyncAgentTeamsClient" in rep
        assert "http://server.test" in rep
        assert "7.5" in rep
        assert "120.0" in rep

    def test_getattr_skips_private_names(self) -> None:
        client = SyncAgentTeamsClient(base_url="http://server.test")
        async_client = client._async
        assert isinstance(async_client, AsyncAgentTeamsClient)
