# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic import ValidationError

from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    ExternalAgentProtocol,
    StdioTransportConfig,
    StreamableHttpTransportConfig,
)


def test_external_agent_config_rejects_a2a_stdio_transport() -> None:
    with pytest.raises(ValidationError, match="streamable_http transport"):
        ExternalAgentConfig(
            agent_id="a2a_agent",
            name="A2A Agent",
            protocol=ExternalAgentProtocol.A2A,
            transport=StdioTransportConfig(command="agent"),
        )


def test_external_agent_config_rejects_cli_http_transport() -> None:
    with pytest.raises(ValidationError, match="stdio transport"):
        ExternalAgentConfig(
            agent_id="cli_agent",
            name="CLI Agent",
            protocol=ExternalAgentProtocol.CLI,
            transport=StreamableHttpTransportConfig(url="http://127.0.0.1:8000/rpc"),
        )
