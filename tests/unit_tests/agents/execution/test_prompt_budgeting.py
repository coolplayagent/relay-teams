# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from relay_teams.agents.execution.prompt_budgeting import (
    MCP_SERVER_CONTEXT_FALLBACK_CHARS,
    PromptBudgetingService,
)
from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerSpec,
    McpToolInfo,
    McpToolSchema,
)
from relay_teams.mcp.mcp_registry import McpRegistry

from .agent_llm_session_test_support import (
    AgentLlmSession,
    ModelEndpointConfig,
    ModelRequest,
    UserPromptPart,
    _build_request,
    _zero_mcp_context_tokens,
)


class _NoSchemaMcpRegistry(McpRegistry):
    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        raise AssertionError("token budgeting should not connect to MCP servers")


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


@pytest.mark.asyncio
async def test_mcp_context_budget_uses_fallback_without_schema_lookup() -> None:
    registry = _NoSchemaMcpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "uvx"}}},
                server_config={"command": "uvx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    service = PromptBudgetingService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        mcp_registry=registry,
        mcp_tool_context_token_cache={},
    )

    estimate = await service.estimated_mcp_context_tokens(
        allowed_mcp_servers=("docs",),
    )

    assert estimate == service.estimated_mcp_context_tokens_fallback(
        allowed_mcp_servers=("docs",)
    )


@pytest.mark.asyncio
async def test_mcp_context_budget_uses_cached_schema_estimate_before_fallback() -> None:
    registry = _NoSchemaMcpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "uvx"}}},
                server_config={"command": "uvx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    service = PromptBudgetingService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        mcp_registry=registry,
        mcp_tool_context_token_cache={"docs": 1234},
    )

    estimate = await service.estimated_mcp_context_tokens(
        allowed_mcp_servers=("docs",),
    )

    assert estimate == 1234


@pytest.mark.asyncio
async def test_mcp_context_budget_uses_discovered_tool_metadata_before_fallback() -> (
    None
):
    registry = _NoSchemaMcpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "uvx"}}},
                server_config={"command": "uvx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    discovery_service = McpDiscoveryService(registry)
    discovery_service.mark_ready(
        "docs",
        (
            McpToolInfo(name="read_file", description="Read a file."),
            McpToolInfo(name="write_file", description="Write a file."),
        ),
    )
    token_cache: dict[str, int] = {}
    service = PromptBudgetingService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        mcp_registry=registry,
        mcp_discovery_service=discovery_service,
        mcp_tool_context_token_cache=token_cache,
    )

    estimate = await service.estimated_mcp_context_tokens(
        allowed_mcp_servers=("docs",),
    )

    assert estimate == service.estimate_mcp_tool_info_tokens(
        server_name="docs",
        tools=discovery_service.get_ready_tools("docs"),
    )
    assert token_cache["docs"] == estimate
    assert estimate < service.estimated_mcp_context_tokens_fallback(
        allowed_mcp_servers=("docs",)
    )


@pytest.mark.asyncio
async def test_mcp_context_budget_fallback_does_not_exhaust_mid_sized_context() -> None:
    registry = _NoSchemaMcpRegistry(
        (
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"command": "uvx"}}},
                server_config={"command": "uvx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    service = PromptBudgetingService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
            context_window=22_000,
        ),
        mcp_registry=registry,
        mcp_tool_context_token_cache={},
    )
    service._config.sampling.max_tokens = 4_096

    max_tokens = await service.safe_max_output_tokens(
        request=_build_request(user_prompt="hello"),
        history=[],
        system_prompt="System prompt",
        reserve_user_prompt_tokens=True,
        allowed_tools=(),
        allowed_mcp_servers=("docs",),
        allowed_skills=(),
    )

    assert max_tokens is not None
    assert max_tokens > 1


def test_mcp_context_budget_fallback_is_bounded_for_unknown_server() -> None:
    service = PromptBudgetingService(
        config=ModelEndpointConfig(
            model="gpt-test",
            base_url="https://example.test/v1",
            api_key="secret",
        ),
        mcp_registry=McpRegistry(),
        mcp_tool_context_token_cache={},
    )
    schemas = tuple(
        McpToolSchema(
            name=f"large_tool_{index}",
            description="Tool with a larger schema payload.",
            input_schema={
                "type": "object",
                "properties": {
                    f"field_{field_index}": {
                        "type": "string",
                        "description": "x" * 400,
                    }
                    for field_index in range(8)
                },
            },
        )
        for index in range(10)
    )

    actual_schema_tokens = service.estimate_mcp_tool_schema_tokens(
        server_name="docs",
        tool_schemas=schemas,
    )
    fallback_tokens = service.estimated_mcp_context_tokens_fallback(
        allowed_mcp_servers=("docs",)
    )

    assert MCP_SERVER_CONTEXT_FALLBACK_CHARS == 8_000
    assert fallback_tokens > 0
    assert actual_schema_tokens > fallback_tokens
