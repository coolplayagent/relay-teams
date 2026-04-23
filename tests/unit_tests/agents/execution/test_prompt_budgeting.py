# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from .agent_llm_session_test_support import (
    AgentLlmSession,
    ModelEndpointConfig,
    ModelRequest,
    UserPromptPart,
    _build_request,
    _zero_mcp_context_tokens,
)


@pytest.mark.asyncio
async def test_safe_max_output_tokens_accounts_for_full_prompt_budget() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=500,
    )
    session._config.sampling.max_tokens = 400
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    max_tokens = await AgentLlmSession._safe_max_output_tokens(
        session,
        request=_build_request(user_prompt="U" * 240),
        history=[ModelRequest(parts=[UserPromptPart(content="hello")])],
        system_prompt="System prompt " + ("S" * 240),
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert max_tokens is not None
    assert 1 <= max_tokens < 400


@pytest.mark.asyncio
async def test_safe_max_output_tokens_returns_configured_value_without_context_window() -> (
    None
):
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=None,
    )
    session._config.sampling.max_tokens = 321

    max_tokens = await AgentLlmSession._safe_max_output_tokens(
        session,
        request=_build_request(user_prompt="hello"),
        history=[],
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert max_tokens == 321


@pytest.mark.asyncio
async def test_safe_max_output_tokens_clamps_to_one_when_budget_is_exhausted() -> None:
    session = object.__new__(AgentLlmSession)
    session._config = ModelEndpointConfig(
        model="gpt-test",
        base_url="https://example.test/v1",
        api_key="secret",
        context_window=10,
    )
    session._config.sampling.max_tokens = 400
    session._estimated_mcp_context_tokens = _zero_mcp_context_tokens
    session._estimated_tool_context_tokens = lambda **_kwargs: 120

    max_tokens = await AgentLlmSession._safe_max_output_tokens(
        session,
        request=_build_request(user_prompt="U" * 200),
        history=[ModelRequest(parts=[UserPromptPart(content="history")])],
        system_prompt="System prompt " + ("S" * 200),
        reserve_user_prompt_tokens=True,
        allowed_tools=("shell",),
        allowed_mcp_servers=(),
        allowed_skills=(),
    )

    assert max_tokens == 1
