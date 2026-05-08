# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from typing import cast

import pytest

from relay_teams.mcp.mcp_models import McpToolSchema
from relay_teams.mcp.mcp_registry import McpRegistry
from relay_teams.mcp.runtime_schema_loader import (
    RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV,
    RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV,
    _remember_runtime_mcp_failure,
    _remember_runtime_mcp_schemas,
    load_runtime_mcp_tool_schemas,
)


class _NoProbeRegistry:
    def __init__(self) -> None:
        self.failed_checks = 0
        self.list_schema_calls = 0

    def is_server_runtime_failed(self, server_name: str) -> bool:
        _ = server_name
        self.failed_checks += 1
        return False

    async def list_tool_schemas(self, server_name: str) -> tuple[McpToolSchema, ...]:
        _ = server_name
        self.list_schema_calls += 1
        raise AssertionError("max_uncached_probes=0 should not probe MCP servers")


class _RetryAfterFailureRegistry:
    def __init__(self) -> None:
        self.runtime_failed_names = {"docs"}
        self.available_names: list[str] = []
        self.list_schema_calls = 0

    def is_server_runtime_failed(self, server_name: str) -> bool:
        return server_name in self.runtime_failed_names

    def mark_server_runtime_failed(self, server_name: str) -> None:
        self.runtime_failed_names.add(server_name)

    def mark_server_runtime_available(self, server_name: str) -> None:
        self.runtime_failed_names.discard(server_name)
        self.available_names.append(server_name)

    async def list_tool_schemas(self, server_name: str) -> tuple[McpToolSchema, ...]:
        self.list_schema_calls += 1
        return (McpToolSchema(name=f"{server_name}_read"),)


@pytest.mark.asyncio
async def test_runtime_mcp_schema_loader_zero_probe_uses_cache_only(
    monkeypatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_MAX_UNCACHED_PROBES_ENV, "0")
    registry = _NoProbeRegistry()
    typed_registry = cast(McpRegistry, registry)
    cached_schema = McpToolSchema(name="cached_tool")
    _remember_runtime_mcp_schemas(typed_registry, "cached", (cached_schema,))

    result = await load_runtime_mcp_tool_schemas(
        mcp_registry=typed_registry,
        server_names=("cached", "missing-1", "missing-2"),
    )

    assert result.schemas_by_server == {"cached": (cached_schema,)}
    assert result.cache_hit_count == 1
    assert result.loaded_server_count == 1
    assert result.probe_skipped_count == 2
    assert registry.failed_checks == 0
    assert registry.list_schema_calls == 0


@pytest.mark.asyncio
async def test_runtime_mcp_schema_loader_retries_after_failure_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(RUNTIME_MCP_SCHEMA_FAILED_TTL_MS_ENV, "1")
    registry = _RetryAfterFailureRegistry()
    typed_registry = cast(McpRegistry, registry)
    _remember_runtime_mcp_failure(typed_registry, "docs")
    await asyncio.sleep(0.05)

    result = await load_runtime_mcp_tool_schemas(
        mcp_registry=typed_registry,
        server_names=("docs",),
    )

    assert result.loaded_server_count == 1
    assert result.runtime_failed_skipped_count == 0
    assert registry.list_schema_calls == 1
    assert registry.is_server_runtime_failed("docs") is False
