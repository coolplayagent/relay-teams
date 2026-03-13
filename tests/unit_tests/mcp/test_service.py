# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agent_teams.mcp.models import (
    McpConfigScope,
    McpServerSpec,
    McpToolInfo,
)
from agent_teams.mcp.registry import McpRegistry
from agent_teams.mcp.service import McpService
from agent_teams.trace import get_trace_context


def test_list_servers_reports_effective_transport() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="remote",
                config={"mcpServers": {"remote": {"url": "https://example.com/sse"}}},
                server_config={"url": "https://example.com/sse"},
                source=McpConfigScope.APP,
            ),
        )
    )

    service = McpService(registry=registry)

    servers = service.list_servers()

    assert [server.name for server in servers] == ["filesystem", "remote"]
    assert [server.transport for server in servers] == ["stdio", "sse"]


def test_list_servers_binds_trace_context(monkeypatch) -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )
    original_list_specs = registry.list_specs

    def traced_list_specs() -> tuple[McpServerSpec, ...]:
        context = get_trace_context()
        assert context.trace_id is not None
        assert context.span_id is not None
        return original_list_specs()

    monkeypatch.setattr(registry, "list_specs", traced_list_specs)
    service = McpService(registry=registry)

    servers = service.list_servers()

    assert [server.name for server in servers] == ["filesystem"]
    assert get_trace_context().trace_id is None


@pytest.mark.asyncio
async def test_list_server_tools_uses_registry_result(monkeypatch) -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        assert name == "filesystem"
        context = get_trace_context()
        assert context.trace_id is not None
        assert context.span_id is not None
        return (
            McpToolInfo(name="read_file", description="Read a file"),
            McpToolInfo(name="write_file", description="Write a file"),
        )

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    service = McpService(registry=registry)

    summary = await service.list_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.transport == "stdio"
    assert [tool.name for tool in summary.tools] == ["read_file", "write_file"]
    assert get_trace_context().trace_id is None
