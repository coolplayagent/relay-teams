# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest
from pydantic import JsonValue

from agent_teams.external_agents.acp_client import StdioAcpTransportClient
from agent_teams.external_agents.models import StdioTransportConfig


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

    async def ignore_message(_message: dict[str, JsonValue]) -> None:
        return None

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
