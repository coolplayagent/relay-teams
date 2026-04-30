# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.external_agents import runtime_probe
from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentTestResult,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)


def _http_agent(protocol: ExternalAgentProtocol) -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id=f"{protocol.value}_agent",
        name="HTTP Agent",
        protocol=protocol,
        transport=StreamableHttpTransportConfig(url="http://127.0.0.1:8000/rpc"),
    )


def _cli_agent() -> ExternalAgentConfig:
    return ExternalAgentConfig(
        agent_id="cli_agent",
        name="CLI Agent",
        protocol=ExternalAgentProtocol.CLI,
        transport=StdioTransportConfig(command="codex"),
    )


@pytest.mark.asyncio
async def test_probe_agent_runtime_dispatches_by_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def probe_a2a_agent(
        config: ExternalAgentConfig,
    ) -> ExternalAgentTestResult:
        return ExternalAgentTestResult(ok=True, message=f"a2a:{config.agent_id}")

    async def probe_cli_agent(
        config: ExternalAgentConfig,
    ) -> ExternalAgentTestResult:
        return ExternalAgentTestResult(ok=True, message=f"cli:{config.agent_id}")

    async def probe_acp_agent(
        config: ExternalAgentConfig,
    ) -> ExternalAgentTestResult:
        return ExternalAgentTestResult(ok=True, message=f"acp:{config.agent_id}")

    monkeypatch.setattr(runtime_probe, "probe_a2a_agent", probe_a2a_agent)
    monkeypatch.setattr(runtime_probe, "probe_cli_agent", probe_cli_agent)
    monkeypatch.setattr(runtime_probe, "probe_acp_agent", probe_acp_agent)

    a2a_result = await runtime_probe.probe_agent_runtime(
        _http_agent(ExternalAgentProtocol.A2A)
    )
    cli_result = await runtime_probe.probe_agent_runtime(_cli_agent())
    acp_result = await runtime_probe.probe_agent_runtime(
        _http_agent(ExternalAgentProtocol.ACP)
    )

    assert a2a_result.message == "a2a:a2a_agent"
    assert cli_result.message == "cli:cli_agent"
    assert acp_result.message == "acp:acp_agent"
