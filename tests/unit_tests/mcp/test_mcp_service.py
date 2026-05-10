# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager
import logging
from pathlib import Path
from types import TracebackType

import pytest
import httpx
from pydantic_ai.mcp import MCPServerSSE, MCPServerStdio, MCPServerStreamableHTTP

from relay_teams.env.proxy_env import ProxyEnvConfig
from relay_teams.mcp.mcp_models import (
    McpConfigScope,
    McpDiscoveryStatus,
    McpServerEnabledUpdateRequest,
    McpServerUpdateRequest,
    McpServerSpec,
    McpToolInfo,
    McpToolSchema,
)
from relay_teams.mcp.mcp_discovery_service import McpDiscoveryService
from relay_teams.mcp.mcp_registry import McpRegistry, build_mcp_server
from relay_teams.mcp.mcp_service import McpService
from relay_teams.mcp.runtime_schema_loader import RuntimeMcpSchemaLoader
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


def test_list_enabled_servers_filters_disabled_servers() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="disabled-docs",
                config={
                    "mcpServers": {"disabled-docs": {"url": "https://example.com/mcp"}}
                },
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
                enabled=False,
            ),
        )
    )

    servers = McpService(registry=registry).list_enabled_servers()

    assert [server.name for server in servers] == ["filesystem"]


def test_list_servers_reads_discovery_summaries_when_available() -> None:
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
    discovery_service = McpDiscoveryService(registry)
    discovery_service.mark_ready(
        "filesystem",
        (McpToolInfo(name="filesystem_read_file", description="Read"),),
    )
    service = McpService(registry=registry, discovery_service=discovery_service)

    servers = service.list_servers()

    assert servers[0].discovery_status == McpDiscoveryStatus.READY
    assert servers[0].tool_count == 1


def test_list_servers_detects_type_aliases_and_unknown_transport() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="local",
                config={"mcpServers": {"local": {"type": "local"}}},
                server_config={"type": "local"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="remote",
                config={
                    "mcpServers": {
                        "remote": {"type": "remote", "url": "https://example.com/sse"}
                    }
                },
                server_config={"type": "remote", "url": "https://example.com/sse"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="custom",
                config={"mcpServers": {"custom": {"type": "custom"}}},
                server_config={"type": "custom"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="unknown",
                config={"mcpServers": {"unknown": {}}},
                server_config={},
                source=McpConfigScope.APP,
            ),
        )
    )

    servers = McpService(registry=registry).list_servers()

    assert [server.transport for server in servers] == [
        "custom",
        "stdio",
        "sse",
        "unknown",
    ]


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
async def test_list_server_tools_reads_cached_discovery_result(monkeypatch) -> None:
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
    discovery_service = McpDiscoveryService(registry)
    discovery_service.mark_ready(
        "filesystem",
        (
            McpToolInfo(name="filesystem_read_file", description="Read a file"),
            McpToolInfo(name="filesystem_write_file", description="Write a file"),
        ),
    )

    async def fake_list_tools(name: str) -> tuple[McpToolInfo, ...]:
        _ = name
        raise AssertionError("list_server_tools should read discovery cache")

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    service = McpService(registry=registry, discovery_service=discovery_service)

    summary = await service.list_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.transport == "stdio"
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == [
        "filesystem_read_file",
        "filesystem_write_file",
    ]
    assert get_trace_context().trace_id is None


@pytest.mark.asyncio
async def test_list_server_tools_without_discovery_service_lists_live_tools(
    monkeypatch,
) -> None:
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

    async def fake_list_tool_schemas(name: str) -> tuple[McpToolSchema, ...]:
        assert name == "filesystem"
        return (
            McpToolSchema(
                name="filesystem_read_file",
                description="Read a file",
                input_schema={"type": "object"},
            ),
        )

    monkeypatch.setattr(registry, "list_tool_schemas", fake_list_tool_schemas)
    service = McpService(registry=registry)

    summary = await service.list_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in summary.tools] == ["filesystem_read_file"]


@pytest.mark.asyncio
async def test_list_server_tools_uses_runtime_schema_loader_cache(monkeypatch) -> None:
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
    calls = 0

    async def fake_list_tool_schemas(name: str) -> tuple[McpToolSchema, ...]:
        nonlocal calls
        assert name == "filesystem"
        calls += 1
        return (
            McpToolSchema(
                name="filesystem_read_file",
                description="Read a file",
                input_schema={"type": "object"},
            ),
        )

    monkeypatch.setattr(registry, "list_tool_schemas", fake_list_tool_schemas)
    loader = RuntimeMcpSchemaLoader(registry, cache_ttl_seconds=60.0)
    service = McpService(registry=registry, runtime_schema_loader=loader)

    first = await service.list_server_tools("filesystem")
    second = await service.list_server_tools("filesystem")

    assert first.status == McpDiscoveryStatus.READY
    assert second.status == McpDiscoveryStatus.READY
    assert calls == 1


@pytest.mark.asyncio
async def test_list_server_tools_returns_failed_summary_during_loader_cooldown(
    monkeypatch,
) -> None:
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

    async def fake_list_tool_schemas(_name: str) -> tuple[McpToolSchema, ...]:
        raise RuntimeError("offline")

    monkeypatch.setattr(registry, "list_tool_schemas", fake_list_tool_schemas)
    loader = RuntimeMcpSchemaLoader(registry, failure_ttl_seconds=60.0)
    service = McpService(registry=registry, runtime_schema_loader=loader)

    first = await service.list_server_tools("filesystem")
    second = await service.list_server_tools("filesystem")

    assert first.status == McpDiscoveryStatus.FAILED
    assert second.status == McpDiscoveryStatus.FAILED
    assert "filesystem" in (second.error or "")
    assert "cooldown" in (second.error or "")


@pytest.mark.asyncio
async def test_refresh_server_tools_enqueues_single_discovery(monkeypatch) -> None:
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
        return (McpToolInfo(name="filesystem_read_file", description="Read a file"),)

    monkeypatch.setattr(registry, "list_tools_for_discovery", fake_list_tools)
    discovery_service = McpDiscoveryService(registry)
    service = McpService(registry=registry, discovery_service=discovery_service)

    summary = service.refresh_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.status == McpDiscoveryStatus.LOADING
    await asyncio.sleep(0)
    loaded = await service.list_server_tools("filesystem")
    assert loaded.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in loaded.tools] == ["filesystem_read_file"]


def test_refresh_server_tools_without_discovery_service_returns_pending() -> None:
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
    service = McpService(registry=registry)

    summary = service.refresh_server_tools("filesystem")

    assert summary.server == "filesystem"
    assert summary.status == McpDiscoveryStatus.PENDING
    assert summary.tools == ()


@pytest.mark.asyncio
async def test_test_server_connection_returns_success(monkeypatch) -> None:
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
        return (McpToolInfo(name="filesystem_read_file", description="Read"),)

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    discovery_service = McpDiscoveryService(registry)
    service = McpService(registry=registry, discovery_service=discovery_service)

    result = await service.test_server_connection("filesystem")
    cached_summary = await service.list_server_tools("filesystem")

    assert result.ok is True
    assert result.tool_count == 1
    assert [tool.name for tool in result.tools] == ["filesystem_read_file"]
    assert cached_summary.status == McpDiscoveryStatus.READY
    assert [tool.name for tool in cached_summary.tools] == ["filesystem_read_file"]


@pytest.mark.asyncio
async def test_test_server_connection_captures_connection_error(monkeypatch) -> None:
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

    async def fake_list_tools(_name: str) -> tuple[McpToolInfo, ...]:
        raise RuntimeError("connection failed")

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    service = McpService(registry=registry)

    result = await service.test_server_connection("filesystem")

    assert result.ok is False
    assert result.error == "connection failed"


@pytest.mark.asyncio
async def test_test_server_connection_marks_discovery_failed_on_error(
    monkeypatch,
) -> None:
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
    discovery_service = McpDiscoveryService(registry)
    discovery_service.mark_ready(
        "filesystem",
        (McpToolInfo(name="filesystem_read_file", description="Read"),),
    )

    async def fake_list_tools(_name: str) -> tuple[McpToolInfo, ...]:
        raise RuntimeError("connection failed")

    monkeypatch.setattr(registry, "list_tools", fake_list_tools)
    service = McpService(registry=registry, discovery_service=discovery_service)

    result = await service.test_server_connection("filesystem")
    cached_summary = await service.list_server_tools("filesystem")

    assert result.ok is False
    assert cached_summary.status == McpDiscoveryStatus.FAILED
    assert cached_summary.tools == ()
    assert cached_summary.error == "RuntimeError: connection failed"


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


class _FailingAsyncToolset:
    async def __aenter__(self) -> "_FailingAsyncToolset":
        raise RuntimeError("MCP startup failed")

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        _ = exc_type, exc, tb

    async def list_tools(self) -> tuple[_FakeListedTool, ...]:
        return ()


class _TimeoutAsyncToolset:
    async def __aenter__(self) -> "_TimeoutAsyncToolset":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        _ = exc_type, exc, tb

    async def list_tools(self) -> tuple[_FakeListedTool, ...]:
        raise TimeoutError()


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

    async def fake_list_tool_objects(
        _name: str,
        *,
        update_runtime_state: bool = True,
        use_cached_toolset: bool = True,
        stdio_default_timeout_seconds: float = 15.0,
    ) -> tuple[_FakeListedTool, ...]:
        _ = update_runtime_state, use_cached_toolset, stdio_default_timeout_seconds
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


@pytest.mark.asyncio
async def test_registry_marks_mcp_server_failed_after_background_load_error(
    monkeypatch,
) -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="broken",
                config={"mcpServers": {"broken": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.build_mcp_server",
        lambda _spec, **_kwargs: _FailingAsyncToolset(),
    )

    with pytest.raises(RuntimeError, match="MCP startup failed"):
        await registry.list_tool_schemas("broken")

    assert registry.is_server_runtime_failed("broken") is True
    assert registry.get_toolsets(("broken",)) == ()


@pytest.mark.asyncio
async def test_registry_discovery_tools_do_not_mark_server_runtime_failed(
    monkeypatch,
) -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="broken",
                config={"mcpServers": {"broken": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
        )
    )

    build_calls = 0

    def fake_build_mcp_server(
        spec: McpServerSpec,
        *,
        proxy_env: Mapping[str, str] | None = None,
        stdio_default_timeout_seconds: float = 15.0,
    ) -> _FailingAsyncToolset:
        nonlocal build_calls
        _ = spec, proxy_env, stdio_default_timeout_seconds
        build_calls += 1
        return _FailingAsyncToolset()

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.build_mcp_server",
        fake_build_mcp_server,
    )

    with pytest.raises(RuntimeError, match="MCP startup failed"):
        await registry.list_tools_for_discovery("broken")

    assert registry.is_server_runtime_failed("broken") is False
    assert build_calls == 1
    assert len(registry.get_toolsets(("broken",))) == 1
    assert build_calls == 2


@pytest.mark.asyncio
async def test_discovery_failure_logs_warning_without_registry_error(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="slow",
                config={"mcpServers": {"slow": {"command": "uvx"}}},
                server_config={
                    "command": "uvx",
                    "args": ["--from", "slow-package", "slow-server"],
                    "transport": "stdio",
                },
                source=McpConfigScope.APP,
            ),
        )
    )

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.build_mcp_server",
        lambda _spec, **_kwargs: _TimeoutAsyncToolset(),
    )
    caplog.set_level(logging.DEBUG, logger="relay_teams.mcp.mcp_registry")
    caplog.set_level(logging.WARNING, logger="relay_teams.mcp.mcp_discovery_service")
    service = McpDiscoveryService(registry)

    service.start_warmup(registry)
    await asyncio.sleep(0)

    summary = service.get_tools_summary("slow")
    assert summary.status == McpDiscoveryStatus.FAILED
    assert summary.error == "TimeoutError: "
    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.ERROR and record.name.endswith("mcp.mcp_registry")
    ] == []
    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name.endswith("mcp.mcp_discovery_service")
    ] == ["MCP tool discovery failed"]


def test_build_mcp_server_uses_runtime_default_stdio_timeout() -> None:
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


def test_build_mcp_server_uses_discovery_default_stdio_timeout() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={"command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
            source=McpConfigScope.SESSION,
        ),
        stdio_default_timeout_seconds=60.0,
    )

    assert isinstance(server, MCPServerStdio)
    assert server.timeout == 60.0
    assert server.read_timeout == 300.0


def test_build_mcp_server_explicit_stdio_timeout_overrides_discovery_default() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "timeout": 22,
            },
            source=McpConfigScope.SESSION,
        ),
        stdio_default_timeout_seconds=60.0,
    )

    assert isinstance(server, MCPServerStdio)
    assert server.timeout == 22.0
    assert server.read_timeout == 300.0


@pytest.mark.asyncio
async def test_remote_sse_mcp_preserves_sdk_timeout(monkeypatch) -> None:
    captured_timeouts: list[httpx.Timeout | None] = []
    sdk_timeout = httpx.Timeout(timeout=5.0, read=300.0)

    class _FakeAsyncClient:
        pass

    def fake_create_async_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        timeout_seconds: float = 5.0,
    ) -> _FakeAsyncClient:
        _ = merged_env, headers, timeout_seconds
        captured_timeouts.append(timeout)
        return _FakeAsyncClient()

    @asynccontextmanager
    async def fake_sse_client(
        url: str,
        *,
        timeout: float = 5.0,
        sse_read_timeout: float | None = None,
        httpx_client_factory,
    ):
        _ = url, timeout, sse_read_timeout
        httpx_client_factory(timeout=sdk_timeout)
        yield object(), object()

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.create_async_http_client",
        fake_create_async_http_client,
    )
    monkeypatch.setattr("relay_teams.mcp.mcp_registry.sse_client", fake_sse_client)

    server = build_mcp_server(
        McpServerSpec(
            name="remote",
            config={"mcpServers": {"remote": {"url": "https://example.com/sse"}}},
            server_config={"url": "https://example.com/sse"},
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(server, MCPServerSSE)
    async with server.client_streams():
        pass

    assert captured_timeouts == [sdk_timeout]


@pytest.mark.asyncio
async def test_remote_mcp_uses_server_specific_proxy_env(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://process-proxy.internal:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://process-proxy.internal:8443")
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.load_proxy_env_config",
        lambda **_kwargs: ProxyEnvConfig(
            http_proxy="http://app-proxy.internal:8080",
            https_proxy="http://app-proxy.internal:8443",
        ),
    )
    captured_envs: list[Mapping[str, str] | None] = []
    captured_headers: list[Mapping[str, str] | None] = []
    captured_timeouts: list[httpx.Timeout | None] = []

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            _ = exc_type, exc, tb

    def fake_create_async_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        timeout_seconds: float = 5.0,
    ) -> _FakeAsyncClient:
        _ = timeout_seconds
        captured_envs.append(merged_env)
        captured_headers.append(headers)
        captured_timeouts.append(timeout)
        return _FakeAsyncClient()

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str,
        *,
        http_client: httpx.AsyncClient,
    ):
        _ = url, http_client
        yield object(), object()

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.create_async_http_client",
        fake_create_async_http_client,
    )
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.streamable_http_client",
        fake_streamable_http_client,
    )

    server = build_mcp_server(
        McpServerSpec(
            name="remote",
            config={"mcpServers": {"remote": {"url": "https://example.com/mcp"}}},
            server_config={
                "url": "https://example.com/mcp",
                "env": {
                    "HTTP_PROXY": "http://127.0.0.1:4879",
                    "HTTPS_PROXY": "http://127.0.0.1:4879",
                },
                "headers": {"Authorization": "Bearer test-token"},
            },
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(server, MCPServerStreamableHTTP)
    async with server.client_streams():
        pass

    assert captured_envs[0] is not None
    assert captured_envs[0]["HTTP_PROXY"] == "http://127.0.0.1:4879"
    assert captured_envs[0]["HTTPS_PROXY"] == "http://127.0.0.1:4879"
    assert captured_headers == [{"Authorization": "Bearer test-token"}]
    assert captured_timeouts[0] is not None
    assert captured_timeouts[0].read == 300.0


@pytest.mark.asyncio
async def test_remote_mcp_without_server_proxy_uses_app_proxy_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://process-proxy.internal:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://process-proxy.internal:8443")
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.load_proxy_env_config",
        lambda **_kwargs: ProxyEnvConfig(
            http_proxy="http://app-proxy.internal:8080",
            https_proxy="http://app-proxy.internal:8443",
        ),
    )
    captured_envs: list[Mapping[str, str] | None] = []
    captured_timeouts: list[httpx.Timeout | None] = []

    class _FakeAsyncClient:
        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            _ = exc_type, exc, tb

    def fake_create_async_http_client(
        *,
        merged_env: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        timeout_seconds: float = 5.0,
    ) -> _FakeAsyncClient:
        _ = headers, timeout_seconds
        captured_envs.append(merged_env)
        captured_timeouts.append(timeout)
        return _FakeAsyncClient()

    @asynccontextmanager
    async def fake_streamable_http_client(
        url: str,
        *,
        http_client: httpx.AsyncClient,
    ):
        _ = url, http_client
        yield object(), object()

    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.create_async_http_client",
        fake_create_async_http_client,
    )
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.streamable_http_client",
        fake_streamable_http_client,
    )

    server = build_mcp_server(
        McpServerSpec(
            name="remote",
            config={"mcpServers": {"remote": {"url": "https://example.com/mcp"}}},
            server_config={"url": "https://example.com/mcp"},
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(server, MCPServerStreamableHTTP)
    async with server.client_streams():
        pass

    assert captured_envs[0] is not None
    assert captured_envs[0]["HTTP_PROXY"] != "http://process-proxy.internal:8080"
    assert captured_envs[0]["HTTP_PROXY"] == "http://app-proxy.internal:8080"
    assert captured_envs[0]["HTTPS_PROXY"] == "http://app-proxy.internal:8443"
    assert captured_timeouts[0] is not None
    assert captured_timeouts[0].read == 300.0


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


def test_build_mcp_server_uses_default_remote_read_timeout() -> None:
    sse_server = build_mcp_server(
        McpServerSpec(
            name="sse",
            config={"mcpServers": {"sse": {"url": "https://example.com/sse"}}},
            server_config={"transport": "sse", "url": "https://example.com/sse"},
            source=McpConfigScope.APP,
        )
    )
    http_server = build_mcp_server(
        McpServerSpec(
            name="http",
            config={"mcpServers": {"http": {"url": "https://example.com/mcp"}}},
            server_config={"transport": "http", "url": "https://example.com/mcp"},
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(sse_server, MCPServerSSE)
    assert isinstance(http_server, MCPServerStreamableHTTP)
    assert sse_server.read_timeout == 300.0
    assert http_server.read_timeout == 300.0


def test_build_mcp_server_accepts_streamable_http_transport_alias() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="docs",
            config={"mcpServers": {"docs": {"url": "https://example.com/mcp"}}},
            server_config={
                "transport": "streamable-http",
                "url": "https://example.com/mcp",
            },
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(server, MCPServerStreamableHTTP)
    assert server.tool_prefix == "docs"


def test_registry_rejects_disabled_server_toolset() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="disabled-docs",
                config={
                    "mcpServers": {"disabled-docs": {"url": "https://example.com/mcp"}}
                },
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
                enabled=False,
            ),
        )
    )

    with pytest.raises(ValueError, match="MCP server is disabled: disabled-docs"):
        registry._get_or_create_toolset("disabled-docs")


def test_build_mcp_server_detects_remote_type_alias() -> None:
    server = build_mcp_server(
        McpServerSpec(
            name="docs",
            config={"mcpServers": {"docs": {"type": "remote"}}},
            server_config={"type": "remote", "url": "https://example.com/mcp"},
            source=McpConfigScope.APP,
        )
    )

    assert isinstance(server, MCPServerStreamableHTTP)


def test_build_mcp_server_stdio_uses_app_proxy_defaults_with_explicit_overrides(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_PROCESS_ONLY", "from-process")
    monkeypatch.setenv("MCP_SHARED_ENV", "from-process")
    monkeypatch.setenv("HTTP_PROXY", "http://process-proxy.internal:8080")
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.load_proxy_env_config",
        lambda **_kwargs: ProxyEnvConfig(
            http_proxy="http://app-proxy.internal:8080",
            https_proxy="http://app-proxy.internal:8443",
            no_proxy="localhost,127.0.0.1",
        ),
    )

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
                    "HTTPS_PROXY": "http://server-proxy.internal:8443",
                },
            },
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.env is not None
    assert server.env["MCP_PROCESS_ONLY"] == "from-process"
    assert server.env["HTTP_PROXY"] != "http://process-proxy.internal:8080"
    assert server.env["HTTP_PROXY"] == "http://app-proxy.internal:8080"
    assert server.env["http_proxy"] == "http://app-proxy.internal:8080"
    assert server.env["NO_PROXY"] == "localhost,127.0.0.1"
    assert server.env["no_proxy"] == "localhost,127.0.0.1"
    assert server.env["NODE_USE_ENV_PROXY"] == "1"
    assert server.env["NPM_CONFIG_PROXY"] == "http://app-proxy.internal:8080"
    assert server.env["npm_config_proxy"] == "http://app-proxy.internal:8080"
    assert server.env["NPM_CONFIG_NOPROXY"] == "localhost,127.0.0.1"
    assert server.env["npm_config_noproxy"] == "localhost,127.0.0.1"
    assert server.env["MCP_SHARED_ENV"] == "from-spec"
    assert server.env["MCP_SPEC_ONLY"] == "from-spec"
    assert server.env["HTTPS_PROXY"] == "http://server-proxy.internal:8443"
    assert server.env["https_proxy"] == "http://server-proxy.internal:8443"
    assert server.env["NPM_CONFIG_HTTPS_PROXY"] == "http://server-proxy.internal:8443"
    assert server.env["npm_config_https_proxy"] == "http://server-proxy.internal:8443"


def test_build_mcp_server_stdio_ignores_process_only_proxy_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_PROCESS_ONLY", "from-process")
    monkeypatch.setenv("HTTP_PROXY", "http://process-proxy.internal:8080")
    monkeypatch.setattr(
        "relay_teams.mcp.mcp_registry.load_proxy_env_config",
        lambda **_kwargs: ProxyEnvConfig(),
    )

    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
            },
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.env is not None
    assert server.env["MCP_PROCESS_ONLY"] == "from-process"
    assert "HTTP_PROXY" not in server.env
    assert "http_proxy" not in server.env
    assert "NPM_CONFIG_PROXY" not in server.env
    assert "npm_config_proxy" not in server.env


def test_build_mcp_server_stdio_expands_explicit_env_references(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "token-from-process")
    monkeypatch.setenv("MCP_HOME", "C:/Users/tester")
    monkeypatch.setenv("MCP_MODE", "stdio")
    monkeypatch.setenv("TOKEN", "same-name-token")

    server = build_mcp_server(
        McpServerSpec(
            name="context7",
            config={"mcpServers": {"context7": {"command": "npx"}}},
            server_config={
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "env": {
                    "TOKEN": "{{MCP_TOKEN}}",
                    "SAME_NAME_TOKEN": "{{TOKEN}}",
                    "CACHE_DIR": "%MCP_HOME%/.cache/context7",
                    "MODE": "$MCP_MODE",
                    "MISSING": "${MCP_MISSING}",
                    "TEMPLATE_MISSING": "{{MCP_MISSING}}",
                },
            },
            source=McpConfigScope.SESSION,
        )
    )

    assert isinstance(server, MCPServerStdio)
    assert server.env is not None
    assert server.env["TOKEN"] == "token-from-process"
    assert server.env["SAME_NAME_TOKEN"] == "same-name-token"
    assert server.env["CACHE_DIR"] == "C:/Users/tester/.cache/context7"
    assert server.env["MODE"] == "stdio"
    assert server.env["MISSING"] == "${MCP_MISSING}"
    assert server.env["TEMPLATE_MISSING"] == "{{MCP_MISSING}}"


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


def test_registry_wildcard_skips_disabled_servers() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="filesystem",
                config={"mcpServers": {"filesystem": {"command": "npx"}}},
                server_config={"command": "npx"},
                source=McpConfigScope.APP,
            ),
            McpServerSpec(
                name="disabled-docs",
                config={
                    "mcpServers": {"disabled-docs": {"url": "https://example.com/mcp"}}
                },
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
                enabled=False,
            ),
        )
    )

    resolved = registry.resolve_server_names(("*",), strict=True)

    assert resolved == ("filesystem",)
    with pytest.raises(ValueError, match="Unknown MCP servers: \\['disabled-docs'\\]"):
        registry.resolve_server_names(("disabled-docs",), strict=True)


def test_list_servers_reports_enabled_state() -> None:
    registry = McpRegistry(
        (
            McpServerSpec(
                name="disabled-docs",
                config={
                    "mcpServers": {"disabled-docs": {"url": "https://example.com/mcp"}}
                },
                server_config={"url": "https://example.com/mcp"},
                source=McpConfigScope.APP,
                enabled=False,
            ),
        )
    )

    servers = McpService(registry=registry).list_servers()

    assert servers[0].enabled is False


def test_add_server_publishes_registry_updates_to_runtime_callback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from relay_teams.mcp.mcp_config_manager import McpConfigManager

    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = McpConfigManager(app_config_dir=app_config_dir)
    discovery_service = McpDiscoveryService(McpRegistry(()))
    published_registries: list[McpRegistry] = []
    replace_count = 0

    original_replace_registry = discovery_service.replace_registry

    def counted_replace_registry(registry: McpRegistry) -> None:
        nonlocal replace_count
        replace_count += 1
        original_replace_registry(registry)

    monkeypatch.setattr(
        discovery_service,
        "replace_registry",
        counted_replace_registry,
    )

    def publish_registry(registry: McpRegistry) -> None:
        published_registries.append(registry)
        service.replace_registry(registry)

    service = McpService(
        registry=McpRegistry(()),
        config_manager=manager,
        on_registry_changed=publish_registry,
        discovery_service=discovery_service,
    )

    service.add_server(
        name="filesystem",
        server_config={"transport": "stdio", "command": "npx"},
    )

    assert len(published_registries) == 1
    assert published_registries[0].get_spec("filesystem").server_config["command"] == (
        "npx"
    )
    assert replace_count == 1


def test_add_server_preserves_extra_specs_during_registry_reload(
    tmp_path: Path,
) -> None:
    from relay_teams.mcp.mcp_config_manager import McpConfigManager

    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = McpConfigManager(app_config_dir=app_config_dir)
    plugin_spec = McpServerSpec(
        name="quality:docs",
        config={"mcpServers": {"quality:docs": {"command": "uvx"}}},
        server_config={"command": "uvx"},
        source=McpConfigScope.PLUGIN,
    )
    service = McpService(
        registry=manager.load_registry(extra_specs=(plugin_spec,)),
        config_manager=manager,
        extra_specs=(plugin_spec,),
    )

    service.add_server(
        name="filesystem",
        server_config={"transport": "stdio", "command": "npx"},
    )

    assert service.list_servers()[0].name == "filesystem"
    assert service.list_servers()[1].name == "quality:docs"


def test_add_server_rejects_plugin_server_shadow(tmp_path: Path) -> None:
    from relay_teams.mcp.mcp_config_manager import McpConfigManager

    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = McpConfigManager(app_config_dir=app_config_dir)
    plugin_spec = McpServerSpec(
        name="quality:docs",
        config={"mcpServers": {"quality:docs": {"command": "uvx"}}},
        server_config={"command": "uvx"},
        source=McpConfigScope.PLUGIN,
    )
    service = McpService(
        registry=manager.load_registry(extra_specs=(plugin_spec,)),
        config_manager=manager,
        extra_specs=(plugin_spec,),
    )

    with pytest.raises(ValueError, match="cannot be shadowed by app config"):
        service.add_server(
            name="quality:docs",
            server_config={"transport": "stdio", "command": "npx"},
            overwrite=True,
        )

    assert not (app_config_dir / "mcp.json").exists()
    assert (
        service.get_server_config("quality:docs").server.source == McpConfigScope.PLUGIN
    )


def test_server_mutations_preserve_extra_specs_during_registry_reload(
    tmp_path: Path,
) -> None:
    from relay_teams.mcp.mcp_config_manager import McpConfigManager

    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = McpConfigManager(app_config_dir=app_config_dir)
    manager.add_server(
        name="filesystem",
        server_config={"transport": "stdio", "command": "npx"},
    )
    plugin_spec = McpServerSpec(
        name="quality:docs",
        config={"mcpServers": {"quality:docs": {"command": "uvx"}}},
        server_config={"command": "uvx"},
        source=McpConfigScope.PLUGIN,
    )
    service = McpService(
        registry=manager.load_registry(extra_specs=(plugin_spec,)),
        config_manager=manager,
        extra_specs=(plugin_spec,),
    )

    service.set_server_enabled(
        "filesystem",
        McpServerEnabledUpdateRequest(enabled=False),
    )
    service.update_server(
        "filesystem",
        McpServerUpdateRequest(config={"transport": "stdio", "command": "bunx"}),
    )

    servers = service.list_servers()
    assert servers[0].name == "filesystem"
    assert servers[0].enabled is False
    assert servers[1].name == "quality:docs"


def test_plugin_server_config_is_readonly_from_runtime_registry(tmp_path: Path) -> None:
    from relay_teams.mcp.mcp_config_manager import McpConfigManager

    app_config_dir = tmp_path / ".agent-teams"
    app_config_dir.mkdir(parents=True)
    manager = McpConfigManager(app_config_dir=app_config_dir)
    plugin_spec = McpServerSpec(
        name="quality:docs",
        config={"mcpServers": {"quality:docs": {"command": "uvx"}}},
        server_config={"command": "uvx"},
        source=McpConfigScope.PLUGIN,
    )
    service = McpService(
        registry=manager.load_registry(extra_specs=(plugin_spec,)),
        config_manager=manager,
        extra_specs=(plugin_spec,),
    )

    result = service.get_server_config("quality:docs")

    assert result.server.name == "quality:docs"
    assert result.server.source == McpConfigScope.PLUGIN
    assert result.config["command"] == "uvx"
    with pytest.raises(ValueError, match="managed by plugin"):
        service.set_server_enabled(
            "quality:docs",
            McpServerEnabledUpdateRequest(enabled=False),
        )
    with pytest.raises(ValueError, match="managed by plugin"):
        service.update_server(
            "quality:docs",
            McpServerUpdateRequest(config={"command": "npx"}),
        )


def test_service_raises_when_config_manager_is_unavailable() -> None:
    service = McpService(registry=McpRegistry(()))

    with pytest.raises(RuntimeError, match="MCP config manager is not available"):
        service.add_server(name="filesystem", server_config={"command": "npx"})
    with pytest.raises(RuntimeError, match="MCP config manager is not available"):
        service.get_server_config("filesystem")
    with pytest.raises(RuntimeError, match="MCP config manager is not available"):
        service.update_server(
            "filesystem",
            McpServerUpdateRequest(config={"command": "uvx"}),
        )
    with pytest.raises(RuntimeError, match="MCP config manager is not available"):
        service.set_server_enabled(
            "filesystem",
            McpServerEnabledUpdateRequest(enabled=False),
        )


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
