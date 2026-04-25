# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.providers import (
    AgentTokenSummary,
    CodeAgentAuthConfig,
    EchoProvider,
    LLMProvider,
    ModelEndpointConfig,
    RunTokenUsage,
    SessionTokenUsage,
)


def test_providers_package_exports_public_symbols() -> None:
    assert AgentTokenSummary.__name__ == "AgentTokenSummary"
    assert RunTokenUsage.__name__ == "RunTokenUsage"
    assert SessionTokenUsage.__name__ == "SessionTokenUsage"
    assert CodeAgentAuthConfig.__name__ == "CodeAgentAuthConfig"
    assert EchoProvider.__name__ == "EchoProvider"
    assert LLMProvider.__name__ == "LLMProvider"
    assert ModelEndpointConfig.__name__ == "ModelEndpointConfig"
