# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from relay_teams.agent_runtimes.clients import acp as acp_module
from relay_teams.agent_runtimes.clients.acp import StdioAcpTransportClient
from relay_teams.agent_runtimes.models import (
    ExternalAgentSecretBinding,
    StdioTransportConfig,
)


@pytest.mark.asyncio
async def test_stdio_transport_starts_in_runtime_workspace(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    async def fake_create_subprocess_exec(
        command: str,
        *args: str,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: str | None,
        env: dict[str, str],
    ) -> object:
        _ = (command, args, stdin, stdout, stderr, env)
        captured["cwd"] = cwd
        raise RuntimeError("stop-after-capture")

    async def ignore_message(
        _method: str,
        _params: dict[str, JsonValue],
        _message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        return {}

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    transport = StdioAcpTransportClient(
        config=StdioTransportConfig(command="codex", args=("--serve",)),
        on_message=ignore_message,
        runtime_cwd="/tmp/project",
    )

    with pytest.raises(RuntimeError, match="stop-after-capture"):
        await transport.start()

    assert captured["cwd"] == "/tmp/project"


@pytest.mark.asyncio
async def test_stdio_transport_applies_w3_auth_token_overlay_to_declared_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_env: dict[str, str] = {}

    async def fake_overlay(
        env: dict[str, str],
        *,
        declared_env: dict[str, object] | None = None,
        **_kwargs: object,
    ) -> dict[str, str]:
        result = dict(env)
        if declared_env is not None and "xAuthToken" in declared_env:
            result["xAuthToken"] = "runtime-token"
        return result

    async def fake_create_subprocess_exec(
        command: str,
        *args: str,
        stdin: int,
        stdout: int,
        stderr: int,
        cwd: str | None,
        env: dict[str, str],
    ) -> object:
        _ = command, args, stdin, stdout, stderr, cwd
        captured_env.update(env)
        raise RuntimeError("stop-after-capture")

    async def ignore_message(
        _method: str,
        _params: dict[str, JsonValue],
        _message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        return {}

    monkeypatch.setattr(acp_module, "overlay_w3_x_auth_token_env", fake_overlay)
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    transport = StdioAcpTransportClient(
        config=StdioTransportConfig(
            command="agent",
            env=(
                ExternalAgentSecretBinding(name="xAuthToken", value=None),
                ExternalAgentSecretBinding(name="WEB_TOKEN", value="keep"),
            ),
        ),
        on_message=ignore_message,
    )

    with pytest.raises(RuntimeError, match="stop-after-capture"):
        await transport.start()

    assert captured_env["xAuthToken"] == "runtime-token"
    assert captured_env["WEB_TOKEN"] == "keep"


@pytest.mark.asyncio
async def test_stdio_transport_routes_inbound_requests_to_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    sent_messages: list[dict[str, JsonValue]] = []

    async def on_message(
        method: str,
        params: dict[str, JsonValue],
        message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        seen["method"] = method
        seen["params"] = params
        seen["message_id"] = message_id
        return {"status": "ok"}

    transport = StdioAcpTransportClient(
        config=StdioTransportConfig(command="codex", args=("--serve",)),
        on_message=on_message,
        runtime_cwd="/tmp/project",
    )

    async def fake_send_raw(message: dict[str, JsonValue]) -> None:
        sent_messages.append(message)

    monkeypatch.setattr(transport, "_send_raw", fake_send_raw)

    await transport._handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "mcp/connect",
            "params": {"serverId": "agent_teams_host_tools"},
        }
    )

    assert seen == {
        "method": "mcp/connect",
        "params": {"serverId": "agent_teams_host_tools"},
        "message_id": 7,
    }
    assert sent_messages == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"status": "ok"},
        }
    ]


@pytest.mark.asyncio
async def test_stdio_transport_send_request_cleans_pending_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ignore_message(
        _method: str,
        _params: dict[str, JsonValue],
        _message_id: str | int | None,
    ) -> dict[str, JsonValue]:
        return {}

    transport = StdioAcpTransportClient(
        config=StdioTransportConfig(command="codex", args=("--serve",)),
        on_message=ignore_message,
        runtime_cwd="/tmp/project",
    )

    async def fake_start() -> None:
        return None

    async def fake_send_raw(message: dict[str, JsonValue]) -> None:
        request_id = message["id"]
        assert isinstance(request_id, int)
        transport._pending[request_id].set_result({"ok": True})

    monkeypatch.setattr(transport, "start", fake_start)
    monkeypatch.setattr(transport, "_send_raw", fake_send_raw)

    result = await transport.send_request("session/new", {})

    assert result == {"ok": True}
    assert transport._pending == {}
