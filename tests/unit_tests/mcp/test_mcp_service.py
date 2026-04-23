# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest
from pydantic_ai.mcp import MCPServerStdio

from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpServerSpec,
    McpToolInfo,
)
from relay_teams.mcp.mcp_registry import McpRegistry, build_mcp_server
from relay_teams.mcp.mcp_service import McpService
from relay_teams.trace import get_trace_context


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
            McpToolInfo(name="filesystem_read_file", description="Read a file"),
            McpToolInfo(name="filesystem_write_file", description="Write a file"),
        )

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    service = McpService(registry=registry)

    summary = await service.list_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.transport == "stdio"
    assert [tool.name for tool in summary.tools] == [
        "filesystem_read_file",
        "filesystem_write_file",
    ]
    assert get_trace_context().trace_id is None


class _FakeListedTool:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, object],
    ) -> None:
        self.name = name
        self.description = description
        self.inputSchema = input_schema


@pytest.mark.asyncio
async def test_registry_list_tools_prefixes_server_name(monkeypatch) -> None:
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

    async def fake_list_tool_objects(_name: str) -> tuple[_FakeListedTool, ...]:
        return (
            _FakeListedTool(
                name="read_file",
                description="Read a file",
                input_schema={"type": "object"},
            ),
            _FakeListedTool(
                name="write_file",
                description="Write a file",
                input_schema={"type": "object"},
            ),
        )

    monkeypatch.setattr(registry, "_list_tool_objects", fake_list_tool_objects)

    tools = await registry.list_tools("filesystem")
    schemas = await registry.list_tool_schemas("filesystem")

    assert [tool.name for tool in tools] == [
        "filesystem_read_file",
        "filesystem_write_file",
    ]
    assert [schema.name for schema in schemas] == [
        "filesystem_read_file",
        "filesystem_write_file",
    ]


def test_build_mcp_server_uses_longer_default_stdio_timeout() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={"command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.tool_prefix == "context7"
    assert server.timeout == 15.0
    assert server.read_timeout == 300.0


def test_build_mcp_server_allows_stdio_timeout_override() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "timeout": 42,
                "read_timeout": 123,
            },
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.tool_prefix == "context7"
    assert server.timeout == 42.0
    assert server.read_timeout == 123.0


def test_build_mcp_server_stdio_inherits_process_env_and_prefers_explicit_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_PROCESS_ONLY", "from-process")
    monkeypatch.setenv("MCP_SHARED_ENV", "from-process")

    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "env": {
                    "MCP_SHARED_ENV": "from-spec",
                    "MCP_SPEC_ONLY": "from-spec",
                },
            },
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.env is not None
    assert server.env["MCP_PROCESS_ONLY"] == "from-process"
    assert server.env["MCP_SHARED_ENV"] == "from-spec"
    assert server.env["MCP_SPEC_ONLY"] == "from-spec"


def test_registry_resolve_server_names_ignores_unknown_servers_when_not_strict() -> (
    None
):
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

    resolved = registry.resolve_server_names(
        ("filesystem", "missing"),
        strict=False,
        consumer="tests.unit_tests.mcp.test_mcp_service",
    )

    assert resolved == ("filesystem",)


def test_registry_resolve_server_names_expands_wildcard() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"url": "https://example.com/mcp"}}},
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
            ),
        )
    )

    resolved = registry.resolve_server_names(("*", "docs"), strict=True)

    assert resolved == ("docs", "filesystem")


def test_registry_resolve_server_names_can_preserve_wildcard() -> None:
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

    resolved = registry.resolve_server_names(
        ("*", "filesystem"),
        strict=True,
        expand_wildcards=False,
    )

    assert resolved == ("*", "filesystem")


def test_registry_resolve_server_names_rejects_partial_wildcard_patterns() -> None:
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

    with pytest.raises(ValueError, match="Unknown MCP servers: \\['file\\*'\\]"):
        registry.resolve_server_names(("file*",), strict=True)


def test_registry_resolve_server_names_filters_unknowns_after_wildcard() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="docs",
                config={"mcpServers": {"docs": {"url": "https://example.com/mcp"}}},
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
            ),
        )
    )

    resolved = registry.resolve_server_names(
        ("*", "missing", "filesystem"),
        strict=False,
    )

    assert resolved == ("docs", "filesystem")


def test_registry_resolve_server_names_reports_unknown_even_with_wildcard() -> None:
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

    with pytest.raises(ValueError, match="Unknown MCP servers: \\['missing'\\]"):
        registry.resolve_server_names(("*", "missing"), strict=True)


def test_registry_resolve_server_names_wildcard_on_empty_registry_is_empty() -> None:
    registry = McpRegistry(())

    assert registry.resolve_server_names(("*",), strict=True) == ()
    assert registry.resolve_server_names(("*", "missing"), strict=False) == ()


def test_registry_resolve_server_names_preserves_wildcard_once_when_not_expanding() -> (
    None
):
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

    resolved = registry.resolve_server_names(
        (" * ", "missing", "*", "filesystem"),
        strict=False,
        expand_wildcards=False,
    )

    assert resolved == ("*", "filesystem")


def test_registry_validate_known_accepts_exact_wildcard_and_rejects_partial() -> None:
    registry = McpRegistry(())

    registry.validate_known(("*",))
    with pytest.raises(ValueError, match="Unknown MCP servers: \\['mcp-\\*'\\]"):
        registry.validate_known(("mcp-*",))
