# -*- coding: utf-8 -*-
from __future__ import annotations

from relay_teams.external_agents.models import (
    ExternalAgentConfig,
    StdioTransportConfig,
)


class TestProviderNativeConfigPaths:
    def test_native_config_fields(self) -> None:
        cfg = ExternalAgentConfig(
            agent_id="a1",
            name="Test",
            description="test agent",
            transport=StdioTransportConfig(command="echo"),
            native_config_enabled=True,
            native_config_provider="anthropic",
            skill_bridge_enabled=True,
            skill_bridge_mode="inline",
        )
        assert cfg.native_config_enabled is True
        assert cfg.native_config_provider == "anthropic"
        assert cfg.skill_bridge_enabled is True
        assert cfg.skill_bridge_mode == "inline"

    def test_native_config_defaults(self) -> None:
        cfg = ExternalAgentConfig(
            agent_id="a2",
            name="Default",
            description="default agent",
            transport=StdioTransportConfig(command="echo"),
        )
        assert cfg.native_config_enabled is False
        assert cfg.native_config_provider == ""
        assert cfg.skill_bridge_enabled is False
        assert cfg.skill_bridge_skills == ()
        assert cfg.skill_bridge_mode == "inline"
