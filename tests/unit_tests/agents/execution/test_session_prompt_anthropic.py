# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.execution.agent_llm_session import AgentLlmSession
from relay_teams.providers.model_config import ModelEndpointConfig, ProviderType


class TestBuildModelSettingsAnthropic:
    """Cover the anthropic branch of _build_model_settings."""

    @pytest.mark.asyncio()
    async def test_anthropic_cache_markers_applied(self) -> None:
        session = object.__new__(AgentLlmSession)
        session._config = ModelEndpointConfig(
            provider=ProviderType.ANTHROPIC,
            model="claude-3-sonnet-20240229",
            base_url="https://api.anthropic.com/v1",
            api_key="test-key",
            context_window=200000,
        )
        session._safe_max_output_tokens = AsyncMock(return_value=4096)  # type: ignore[assignment]

        result = await session._build_model_settings(
            request=AsyncMock(thinking=AsyncMock(enabled=False, effort=None)),
            history=[],
            system_prompt="x" * 5000,
            reserve_user_prompt_tokens=False,
            allowed_tools=(),
            allowed_mcp_servers=(),
            allowed_skills=(),
        )
        assert isinstance(result, dict)
        assert result.get("max_tokens") == 4096
        extra = result.get("extra_body")
        assert isinstance(extra, dict)
        assert "prompt-caching-2024-07-31" in extra.get("anthropic_beta", [])
