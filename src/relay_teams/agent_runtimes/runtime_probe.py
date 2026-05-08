# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from relay_teams.agent_runtimes.clients.a2a import probe_a2a_agent
from relay_teams.agent_runtimes.clients.acp import probe_acp_agent
from relay_teams.agent_runtimes.clients.cli import probe_cli_agent
from relay_teams.agent_runtimes.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    ExternalAgentTestResult,
)


async def probe_agent_runtime(
    config: ExternalAgentConfig,
    *,
    runtime_cwd: Path | None = None,
) -> ExternalAgentTestResult:
    if config.protocol == ExternalAgentProtocol.A2A:
        return await probe_a2a_agent(config)
    if config.protocol == ExternalAgentProtocol.CLI:
        return await probe_cli_agent(config, runtime_cwd=runtime_cwd)
    return await probe_acp_agent(config)
