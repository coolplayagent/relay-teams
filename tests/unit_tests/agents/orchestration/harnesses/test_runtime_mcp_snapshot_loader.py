# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

import pytest

from relay_teams.mcp.runtime_schema_loader import (
    RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV,
    RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV,
    RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS_ENV,
    RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV,
    RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV,
    load_runtime_mcp_tool_schemas,
)
from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry


class _CountingMcpRegistry(McpRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        self.calls.append(name)
        return (
            McpToolSchema(
                name=f"{name}_search",
                description="Search docs",
                input_schema={"type": "object"},
            ),
        )


class _SlowMcpRegistry(McpRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        self.calls.append(name)
        await asyncio.sleep(0.05)
        return (
            McpToolSchema(
                name=f"{name}_search",
                description="Search docs",
                input_schema={"type": "object"},
            ),
        )


class _FailingMcpRegistry(McpRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def list_tool_schemas(self, name: str) -> tuple[McpToolSchema, ...]:
        self.calls.append(name)
        raise RuntimeError("MCP startup failed")


@pytest.mark.asyncio
async def test_runtime_mcp_snapshot_loader_uses_fresh_schema_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_CACHE_TTL_MS_ENV, "60000")
    registry = _CountingMcpRegistry()

    first = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("docs",),
    )
    second = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("docs",),
    )

    assert registry.calls == ["docs"]
    assert first.loaded_server_count == 1
    assert second.cache_hit_count == 1
    assert second.schemas_by_server["docs"][0].name == "docs_search"


@pytest.mark.asyncio
async def test_runtime_mcp_snapshot_loader_bounds_slow_server_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_LOAD_BUDGET_MS_ENV, "40")
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_SERVER_TIMEOUT_MS_ENV, "10")
    registry = _SlowMcpRegistry()

    started = asyncio.get_running_loop().time()
    result = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("slow_docs", "slow_search"),
    )
    elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1000)

    assert elapsed_ms < 80
    assert result.timeout_count == 2
    assert result.loaded_server_count == 0
    assert registry.calls == ["slow_docs", "slow_search"]


@pytest.mark.asyncio
async def test_runtime_mcp_snapshot_loader_can_skip_all_uncached_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV, "0")
    registry = _CountingMcpRegistry()

    result = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("docs", "search"),
    )

    assert registry.calls == []
    assert result.probe_skipped_count == 2
    assert result.loaded_server_count == 0


@pytest.mark.asyncio
async def test_runtime_mcp_snapshot_loader_caches_failed_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV, "60000")
    registry = _FailingMcpRegistry()

    first = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("broken",),
    )
    second = await load_runtime_mcp_tool_schemas(
        mcp_registry=registry,
        server_names=("broken",),
    )

    assert registry.calls == ["broken"]
    assert first.failure_count == 1
    assert second.runtime_failed_skipped_count == 1
