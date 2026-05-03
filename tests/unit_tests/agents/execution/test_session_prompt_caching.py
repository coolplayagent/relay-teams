# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from relay_teams.agents.execution.session_prompt import SessionPromptMixin
from relay_teams.providers.model_config import ModelEndpointConfig, SamplingConfig


def _make_anthropic_config() -> ModelEndpointConfig:
    return ModelEndpointConfig(
        provider="anthropic",
        model="claude-3-sonnet-20240229",
        base_url="https://api.anthropic.com/v1",
        api_key="test-key",
        context_window=200000,
        sampling=SamplingConfig(temperature=0.7, top_p=1.0),
    )


def _make_openai_config() -> ModelEndpointConfig:
    return ModelEndpointConfig(
        provider="openai",
        model="gpt-4",
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        context_window=128000,
        sampling=SamplingConfig(temperature=0.7, top_p=1.0),
    )


class _FakeSession(SessionPromptMixin):
    """Minimal concrete subclass for testing _build_model_settings."""

    def __init__(self) -> None:
        self._config: ModelEndpointConfig = _make_anthropic_config()
        self._estimated_mcp_context_tokens = lambda: 0


@pytest.fixture()
def session() -> _FakeSession:
    return _FakeSession()


class TestBuildModelSettingsAnthropic:
    """Cover _build_model_settings anthropic branch + apply_anthropic_cache_markers."""

    @pytest.mark.asyncio()
    async def test_anthropic_calls_cache_markers(self, session: _FakeSession) -> None:
        session._config = _make_anthropic_config()
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
        assert result["max_tokens"] == 4096
        extra = result.get("extra_body")
        assert isinstance(extra, dict)
        assert "prompt-caching-2024-07-31" in extra.get("anthropic_beta", [])

    @pytest.mark.asyncio()
    async def test_anthropic_short_prompt_no_caching(
        self, session: _FakeSession
    ) -> None:
        session._config = _make_anthropic_config()
        session._safe_max_output_tokens = AsyncMock(return_value=4096)  # type: ignore[assignment]

        result = await session._build_model_settings(
            request=AsyncMock(thinking=AsyncMock(enabled=False, effort=None)),
            history=[],
            system_prompt="short",
            reserve_user_prompt_tokens=False,
            allowed_tools=(),
            allowed_mcp_servers=(),
            allowed_skills=(),
        )
        assert isinstance(result, dict)
        assert result.get("extra_body") is None

    @pytest.mark.asyncio()
    async def test_anthropic_with_thinking(self, session: _FakeSession) -> None:
        session._config = _make_anthropic_config()
        session._safe_max_output_tokens = AsyncMock(return_value=None)  # type: ignore[assignment]

        result = await session._build_model_settings(
            request=AsyncMock(thinking=AsyncMock(enabled=True, effort="high")),
            history=[],
            system_prompt="short",
            reserve_user_prompt_tokens=False,
            allowed_tools=(),
            allowed_mcp_servers=(),
            allowed_skills=(),
        )
        assert isinstance(result, dict)
        assert result.get("thinking") == "high"
